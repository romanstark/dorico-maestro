"""FastMCP server exposing Dorico Maestro's high-level musical tools.

This is the MCP surface described in ``docs/architecture.md``: a handful of
*intent* tools (add notes, transpose, playback…), a generic ``run_command``
escape hatch, and a ``dorico://commands`` resource so an assistant can discover
the ~340 catalogued commands without carrying a tool definition for each.

Every tool routes through :func:`dorico_maestro.executor.execute` against the
shared :func:`dorico_maestro.registry.default_registry`, except the note-entry
tools, which drive :class:`dorico_maestro.session.NoteInputSession` directly
(the caret lifecycle lives there).

Honesty note: Dorico's ``kOK`` means *accepted*, not that the intended musical
effect happened. Tool results echo the response ``code`` and, where a verifier
exists, a ``verified`` flag — but callers should still confirm via
``get_status``, playback, or the score itself. See ``docs/protocol.md``.

Run standalone (stdio):  python -m dorico_maestro.server
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

from mcp.server.fastmcp import FastMCP

from dorico_maestro import render
from dorico_maestro.client import DoricoClient, DoricoConnectionError
from dorico_maestro.executor import Result, execute
from dorico_maestro.models import CmdStatus, NoteDuration
from dorico_maestro.music import musicxml, theory
from dorico_maestro.music.score import (
    ScoreSpec,
    ScoreSpecError,
    score_from_dict,
    spec_schema,
    total_events,
)
from dorico_maestro.registry import default_registry
from dorico_maestro.session import NoteInputSession
from dorico_maestro.spec import CommandSpec

logging.basicConfig(level=logging.INFO)  # to stderr — stdout is the stdio channel

_KOK_CAVEAT = (
    "'kOK' means Dorico accepted the command, not that the musical effect "
    "happened — verify via get_status, playback, or the score."
)

# Dorico rhythmic-grid value -> quarter-length, for dead-reckoning beat offsets.
_GRID_QL: dict[str, float] = {
    "kSemibreve": 4.0, "kMinim": 2.0, "kCrotchet": 1.0, "kQuaver": 0.5,
    "kSemiQuaver": 0.25, "kDemiSemiQuaver": 0.125, "kHemiDemiSemiQuaver": 0.0625,
}

# Selected-note duration value -> friendly name (read_selection).
_DORICO_DURATION_NAME: dict[str, str] = {
    "kSemibreve": "whole", "kMinim": "half", "kCrotchet": "quarter",
    "kQuaver": "eighth", "kSemiQuaver": "sixteenth",
    "kDemiSemiQuaver": "32nd", "kHemiDemiSemiQuaver": "64th",
}

# Status articulation flag -> friendly name (read_selection).
_STATUS_ARTICULATIONS: list[tuple[str, str]] = [
    ("articulationAccent", "accent"),
    ("articulationStaccato", "staccato"),
    ("articulationMarcato", "marcato"),
    ("articulationTenuto", "tenuto"),
    ("articulationStaccatissimo", "staccatissimo"),
    ("articulationStaccatoTenuto", "staccato-tenuto"),
    ("articulationStressed", "stress"),
    ("articulationUnstressed", "unstress"),
]

_MODE_ALIASES: dict[str, str] = {
    "write": "kWriteMode",
    "engrave": "kEngraveMode",
    "play": "kPlayMode",
    "print": "kPrintMode",
    "setup": "kSetupMode",
}
_MODE_VALUES = set(_MODE_ALIASES.values())


# --------------------------------------------------------------- client singleton
_client: DoricoClient | None = None


def _client_instance() -> DoricoClient:
    """Return the lazily-created, process-wide Dorico client."""
    global _client
    if _client is None:
        _client = DoricoClient()
    return _client


mcp = FastMCP(
    name="dorico-maestro",
    instructions=(
        "Compose together inside Steinberg Dorico. Always connect_to_dorico() first. "
        "Reads are selection-only; use get_status for state. 'kOK' means accepted, not "
        "necessarily effective — verify. Discover the full command set via the "
        "dorico://commands resource; run anything with run_command. See docs/protocol.md."
    ),
)


# ------------------------------------------------------------------------ helpers
def _parse_duration(duration: str) -> NoteDuration:
    return NoteDuration(duration.lower())


def _duration_error(duration: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": f"invalid duration {duration!r}; use one of {[d.value for d in NoteDuration]}",
    }


def _result_dict(
    result: Result, registry_status: CmdStatus | None = None, **extra: Any
) -> dict[str, Any]:
    """Render an executor :class:`Result` as a plain, honest ``{success, …}`` dict."""
    out: dict[str, Any] = {
        "success": bool(result.ok) and not result.blocked,
        "command": result.command,
        "code": result.code,
    }
    if result.detail is not None:
        out["detail"] = result.detail
    if result.verified is not None:
        out["verified"] = result.verified
    if result.blocked:
        out["blocked"] = True
        out["message"] = result.message
    if registry_status is not None:
        out["registry_status"] = registry_status.value
        if registry_status is not CmdStatus.VERIFIED and not result.blocked:
            out["caveat"] = _KOK_CAVEAT
    out.update(extra)
    return out


async def _run(cmd_id: str, *, confirm: bool = False, **args: Any) -> dict[str, Any]:
    """Execute one catalogued command and return a ``{success, …}`` dict.

    Centralises the error handling every high-level tool and ``run_command``
    share: an unknown command id, a missing/unreadable catalog, a validation
    error from the builder, or an inability to reach Dorico.
    """
    client = _client_instance()
    try:
        registry = default_registry()
        spec = registry.get(cmd_id)
    except KeyError:
        return {"success": False, "error": f"unknown command {cmd_id!r} (not in the registry)"}
    except OSError as e:
        return {"success": False, "error": f"could not load the command catalog: {e}"}
    try:
        result = await execute(client, registry, cmd_id, confirm=confirm, **args)
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}
    except ValueError as e:
        return {"success": False, "error": f"invalid arguments for {cmd_id}: {e}"}
    return _result_dict(result, spec.status)


def _load_spec(score: dict[str, Any]) -> tuple[ScoreSpec | None, dict[str, Any] | None]:
    """Parse a score-spec dict into a :class:`ScoreSpec`.

    Returns ``(spec, None)`` on success or ``(None, error_dict)`` when the spec is
    invalid — an honest ``{success: False, error: …}`` from the path-tagged
    :class:`ScoreSpecError`. Invalid specs send nothing to Dorico.
    """
    try:
        return score_from_dict(score), None
    except ScoreSpecError as e:
        return None, {"success": False, "error": str(e)}


def _zero_notes_error() -> dict[str, Any]:
    """The loud diagnostic for a spec that parses but carries no notes."""
    return {
        "success": False,
        "error": (
            "Parsed OK but the score has 0 notes — your events are probably in the "
            "wrong place. A part needs either a flat 'events' list or nested "
            "'staves' -> 'voices' -> 'events'. Call score_schema for the exact shape."
        ),
        "example": spec_schema()["minimal_flat"],
    }


async def _position_caret(client: Any, bar: int, staff: int) -> None:
    """Deterministically drive the caret to ``bar`` (1-based) of ``staff`` (0-based).

    Enters note input **only if it is not already active** — a second
    ``NoteInput.Enter`` toggles note input back OFF, which would leave the following
    caret moves with no caret and raise a Dorico error. Then rewinds to bar 1 of the
    top staff and advances. Always re-anchors from bar 1; leaves note input active.
    """
    status = await client.status()
    if not status.get("noteInputActive"):
        await client.send("NoteInput.Enter")
    await client.send("NoteInput.MoveUpTop")
    for _ in range(64):
        await client.send("NoteInput.MoveLeftBar")
    for _ in range(max(bar - 1, 0)):
        await client.send("NoteInput.MoveRightBar")
    for _ in range(max(staff, 0)):
        await client.send("NoteInput.MoveDown")


def _report_dict(report: render.RenderReport) -> dict[str, Any]:
    """Serialise a :class:`render.RenderReport` as a plain JSON-friendly dict."""
    return {
        "ok": report.ok,
        "parts_rendered": report.parts_rendered,
        "commands_planned": report.commands_planned,
        "commands_sent": report.commands_sent,
        "warnings": report.warnings,
        "experimental": report.experimental,
    }


def _temp_musicxml_path() -> str:
    """Create and return the path to a fresh empty ``.musicxml`` temp file."""
    fd, path = tempfile.mkstemp(suffix=".musicxml")
    os.close(fd)
    return path


def _spec_payload(spec: CommandSpec) -> dict[str, Any]:
    """Serialise a :class:`CommandSpec` for the ``dorico://commands`` resource."""
    return {
        "id": spec.id,
        "category": spec.category,
        "status": spec.status.value,
        "requires_note_input": spec.requires_note_input,
        "destructive": spec.destructive,
        "params": [
            {
                "name": p.name,
                "dorico": p.dorico,
                "kind": p.kind,
                "enum": p.enum,
                "required": p.required,
            }
            for p in spec.params
        ],
        "doc": spec.doc,
    }


# ------------------------------------------------------------------ connection
@mcp.tool()
async def connect_to_dorico() -> dict[str, Any]:
    """Connect to Dorico and return its live status.

    The first connection shows an approval dialog inside Dorico — the composer
    accepts it once, then the session token is reused on later runs.
    """
    try:
        client = _client_instance()
        await client.connect()
        return {"success": True, "status": await client.status()}
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_status() -> dict[str, Any]:
    """Return Dorico's current (pushed) application status snapshot.

    Reads are selection-only: this reflects the merged status deltas Dorico
    pushes (window mode, note-input state, current selection, ``canUndo``, …),
    not an arbitrary query of the score.
    """
    try:
        client = _client_instance()
        return {"success": True, "status": await client.status()}
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def read_selection() -> dict[str, Any]:
    """Read the PROPERTIES of the currently selected note/event — not its position.

    The Remote API exposes a selected event's rhythmic properties in the pushed
    status (duration, dots, articulations, accidental, event type) but NOT its
    pitch and NOT its bar/beat position — those cannot be read (use goto_bar for
    dead-reckoned positioning). Select a note in Dorico, then call this to learn
    what kind of note it is. Returns ``{success, has_selection, event_type,
    duration, dots, articulations, accidental}``.
    """
    client = _client_instance()
    try:
        await client.connect()
        st = await client.status()
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}
    dur = st.get("duration") or ""
    return {
        "success": True,
        "has_selection": bool(st.get("hasSelection")),
        "event_type": st.get("selectedEventType"),
        "duration": _DORICO_DURATION_NAME.get(dur, dur or None),
        "dots": int(str(st.get("rhythmDots", "0")) or "0"),
        "articulations": [name for flag, name in _STATUS_ARTICULATIONS if st.get(flag)],
        "accidental": st.get("accidental") or None,
        "note": "The API does not expose the selection's pitch or bar/beat position.",
    }


# ------------------------------------------------------------------- note entry
@mcp.tool()
async def add_notes(
    notes: list[str],
    duration: str = "quarter",
    as_chord: bool = False,
) -> dict[str, Any]:
    """Input notes at the caret, then leave note-input mode cleanly.

    ONE insertion at the current caret. For a SEQUENCE of notes or chords over
    time, use ``write_score`` / ``render_to_dorico`` with a ScoreSpec (a chord is
    one event with >=2 pitches) — repeated ``add_notes`` calls do NOT chain: each
    re-enters note input at the same spot, so successive chords stack on one beat.

    Args:
        notes: pitches like ``["C4", "E4", "G4"]`` (letter + optional #/b + octave).
        duration: whole | half | quarter | eighth | sixteenth | 32nd | 64th.
        as_chord: if true, stack the notes as one chord instead of a sequence.

    Uses :class:`NoteInputSession`, so note input is always exited even on error.
    Success only means the commands were accepted (kOK) — confirm the notes
    actually landed via get_status/playback/your eyes.
    """
    if not notes:
        return {"success": False, "error": "no notes given"}
    try:
        dur = _parse_duration(duration)
    except ValueError:
        return _duration_error(duration)

    client = _client_instance()
    try:
        await client.connect()
        async with NoteInputSession(client) as session:
            await session.set_duration(dur)
            if as_chord and len(notes) > 1:
                # Chord mode (Q): stack subsequent pitches on the same beat.
                await client.send("NoteInput.StartEndChord")
                try:
                    for note in notes:
                        await session.pitch(note)
                finally:
                    await client.send("NoteInput.StartEndChord")
            else:
                for note in notes:
                    await session.pitch(note)
        status = await client.status()
        return {
            "success": True,
            "notes": notes,
            "duration": dur.value,
            "as_chord": as_chord,
            "can_undo": status.get("canUndo"),
            "note": _KOK_CAVEAT,
        }
    except ValueError as e:  # malformed pitch string — caret already exited
        return {"success": False, "error": str(e), "notes": notes}
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def add_rest(duration: str = "quarter") -> dict[str, Any]:
    """Input a rest of the given duration at the caret, then leave note input.

    duration: whole | half | quarter | eighth | sixteenth | 32nd | 64th.
    Accepted (kOK) does not prove the rest landed — verify.
    """
    try:
        dur = _parse_duration(duration)
    except ValueError:
        return _duration_error(duration)

    client = _client_instance()
    try:
        await client.connect()
        async with NoteInputSession(client) as session:
            await session.set_duration(dur)
            await session.rest()
        status = await client.status()
        return {
            "success": True,
            "duration": dur.value,
            "can_undo": status.get("canUndo"),
            "verified": False,
            "note": (
                "UNVERIFIED: rest entry sends NoteInput.RestMode, which is a toggle — a "
                "single send may only arm rest input rather than place a rest, and it may "
                "stay armed. Confirm in the score. " + _KOK_CAVEAT
            ),
        }
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


# --------------------------------------------------------------------- editing
@mcp.tool()
async def transpose(
    direction: str,
    chromatic: bool = False,
    octave: bool = False,
) -> dict[str, Any]:
    """Transpose the current selection up or down.

    Args:
        direction: "up" or "down".
        chromatic: step by a chromatic semitone instead of diatonically.
        octave: move by a whole octave (takes precedence over ``chromatic``).

    Operates on whatever is selected in Dorico (reads are selection-only), so
    make a selection first. Maps to ``NoteEdit.Pitch{Up,Down}[Chromatic|Octave]``.
    """
    d = direction.lower()
    if d not in ("up", "down"):
        return {"success": False, "error": "direction must be 'up' or 'down'"}
    up_down = "Up" if d == "up" else "Down"
    if octave:
        cmd_id = f"NoteEdit.Pitch{up_down}Octave"
    elif chromatic:
        cmd_id = f"NoteEdit.Pitch{up_down}Chromatic"
    else:
        cmd_id = f"NoteEdit.Pitch{up_down}"
    return await _run(cmd_id)


@mcp.tool()
async def set_time_signature(signature: str = "4/4") -> dict[str, Any]:
    """Set a time signature — NOT yet supported over the API.

    In Dorico a time signature is typed into a popover, which the Remote Control
    API can't fill, so the value can't be transmitted. Rather than silently drop
    it, this tool reports the limitation. signature: e.g. "4/4", "3/4", "6/8".
    """
    return {
        "success": False,
        "supported": False,
        "requested": signature,
        "error": (
            "Setting a specific time signature over the API isn't supported yet: "
            "Dorico enters it via a popover and the value can't be sent. Set it in "
            "Dorico directly, or run_command('NoteInput.CreateTimeSignature') to open "
            "the popover. (Tracked as an open item in docs/architecture.md.)"
        ),
    }


@mcp.tool()
async def set_key_signature(key: str = "C major") -> dict[str, Any]:
    """Set a key signature — NOT yet supported over the API.

    Like the time signature, this is a popover action in Dorico; the tonality
    can't be transmitted over the Remote Control API. key: e.g. "C major",
    "G major", "F minor".
    """
    return {
        "success": False,
        "supported": False,
        "requested": key,
        "error": (
            "Setting a specific key signature over the API isn't supported yet: "
            "Dorico enters it via a popover and the value can't be sent. Set it in "
            "Dorico directly, or run_command('NoteInput.CreateKeySignature') to open "
            "the popover. (Tracked as an open item in docs/architecture.md.)"
        ),
    }


@mcp.tool()
async def navigate(target: str, bar: int | None = None) -> dict[str, Any]:
    """Move around the score.

    Args:
        target: "start", "end", or "bar".
        bar: 1-indexed bar number (only meaningful with target="bar").

    "start"/"end" move the *viewport* (not the caret). "bar" is not yet
    supported: Edit.GoTo opens a dialog whose field the API can't fill.
    """
    t = target.lower()
    if t == "bar":
        if bar is None:
            return {"success": False, "error": "target 'bar' requires bar=<int>"}
        return {
            "success": False,
            "supported": False,
            "requested_bar": bar,
            "error": (
                "Jumping to a specific bar isn't supported over the API yet: Edit.GoTo "
                "opens Dorico's Go To dialog and the bar number can't be filled in. Use "
                "run_command('Edit.GoTo') to open it, then type the bar in Dorico."
            ),
        }
    if t in ("start", "end"):
        cmd_id = "View.MoveViewportToStart" if t == "start" else "View.MoveViewportToEnd"
        result = await _run(cmd_id)
        result.setdefault(
            "note",
            "Moves the viewport only; it does not move the caret/selection.",
        )
        return result
    return {"success": False, "error": "target must be 'start', 'end', or 'bar'"}


# ----------------------------------------------------------- window & transport
@mcp.tool()
async def switch_mode(mode: str) -> dict[str, Any]:
    """Switch Dorico's window mode.

    mode: "write" | "engrave" | "play" | "print" | "setup" (the ``kWriteMode``…
    forms are also accepted). Maps to ``Window.SwitchMode``.
    """
    key = mode if mode in _MODE_VALUES else _MODE_ALIASES.get(mode.lower())
    if key not in _MODE_VALUES:
        return {
            "success": False,
            "error": f"unknown mode {mode!r}; choose from {sorted(_MODE_ALIASES)}",
        }
    return await _run("Window.SwitchMode", mode=key)


@mcp.tool()
async def playback(action: str = "play", location: str = "kPlayhead") -> dict[str, Any]:
    """Start, stop, or rewind playback.

    Args:
        action: "play", "stop", or "rewind". "rewind" stops playback **and** returns
            the playhead to the start of the flow, so you don't have to stop and
            rewind by hand after the music runs on into empty trailing bars.
        location: where "play" starts from — kPlayhead | kSelection | kStartOfFlow
            | kLastStartPosition.

    Maps to ``Play.StartOrStop`` / ``Play.Stop`` / ``Play.SetPlayheadToFlowStart``.
    """
    a = action.lower()
    if a == "stop":
        return await _run("Play.Stop")
    if a == "play":
        return await _run("Play.StartOrStop", location=location)
    if a == "rewind":
        stop = await _run("Play.Stop")
        rewind = await _run("Play.SetPlayheadToFlowStart")
        return {
            "success": bool(stop.get("success")) and bool(rewind.get("success")),
            "action": "rewind",
            "stop": stop,
            "rewind": rewind,
        }
    return {"success": False, "error": "action must be 'play', 'stop', or 'rewind'"}


@mcp.tool()
async def save() -> dict[str, Any]:
    """Save the current project (``File.Save``)."""
    return await _run("File.Save")


@mcp.tool()
async def goto_bar(bar: int, staff: int = 0, beat: float = 1.0) -> dict[str, Any]:
    """Move the caret to a bar (and optionally a beat within it) and report where it is.

    The Remote API cannot *read* the caret position — the status carries no bar or
    beat — so this establishes a KNOWN one by dead-reckoning: it enters note input,
    rewinds to bar 1 of the top staff, then advances to ``bar`` (1-based) and down
    to ``staff`` (0-based). Because it re-anchors from bar 1, the result does not
    depend on where the caret was, so call it before each write batch — a manual
    edit in Dorico invalidates any earlier assumption. Returns the ASSUMED
    ``{bar, staff, beat}``. Leaves note input active so a following add_notes/add_rest
    writes from here.

    ``beat`` (1-based, in quarter-note beats; 1.0 = the downbeat) advances within
    the bar by stepping over Dorico's rhythmic grid (``rhythmicGridResolutionValue``);
    the returned ``beat`` is snapped to that grid. The whole position is
    dead-reckoned (not read back from Dorico), so confirm for unusual rhythms.
    """
    if bar < 1:
        return {"success": False, "error": "bar must be >= 1 (bars are 1-based)"}
    if staff < 0:
        return {"success": False, "error": "staff must be >= 0 (staves are 0-based)"}
    if beat < 1:
        return {"success": False, "error": "beat must be >= 1 (beats are 1-based)"}
    client = _client_instance()
    try:
        await client.connect()
        await _position_caret(client, bar, staff)
        landed_beat = 1.0
        if beat > 1:
            status = await client.status()
            grid_ql = _GRID_QL.get(status.get("rhythmicGridResolutionValue", "kQuaver"), 0.5)
            steps = round((beat - 1.0) / grid_ql)
            for _ in range(max(steps, 0)):
                await client.send("NoteInput.MoveRight")
            landed_beat = 1.0 + steps * grid_ql
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}
    return {
        "success": True,
        "caret": {"bar": bar, "staff": staff, "beat": landed_beat},
        "assumed": True,
        "caveat": (
            "Position is dead-reckoned, not read from Dorico — correct as long as no "
            "manual edit moved the caret since. Beat is snapped to the rhythmic grid."
        ),
    }


@mcp.tool()
async def open_popover(kind: str, bar: int | None = None, staff: int = 0) -> dict[str, Any]:
    """Open a Dorico input popover (dynamic/tempo/key/time/clef) for the user to fill.

    The Remote API cannot *type* into Dorico's Shift+D/K/T popovers, but it can
    *open* them — so this is a guided, assisted flow: it optionally moves the caret
    to ``bar`` (1-based) of ``staff`` (0-based), fires the matching
    ``NoteInput.Create*`` command to open the popover, and returns an
    ``instruction`` telling the user exactly what to type + Enter. It deliberately
    leaves note input / the popover **open** so the user can type; Esc cancels.

    EXPERIMENTAL: positioning by bar uses note-input caret moves and is unverified
    across layouts — confirm the spot in Dorico. Omit ``bar`` to open the popover
    at the current selection (select the target note first). For a hands-free,
    fully rendered result, prefer the MusicXML path
    (``write_score(method="musicxml")``), which carries key/dynamics/tempo exactly.
    """
    kinds = {
        "dynamic": ("NoteInput.CreateDynamic", "a dynamic, e.g. 'mf', 'ff', 'pp'"),
        "tempo": ("NoteInput.CreateTempo", "a tempo, e.g. 'Allegro' or 'q=120'"),
        "key": ("NoteInput.CreateKeySignature", "a key, e.g. 'G', 'F', 'Am'"),
        "time": ("NoteInput.CreateTimeSignature", "a time signature, e.g. '3/4'"),
        "clef": ("NoteInput.CreateClef", "a clef, e.g. 'treble' or 'bass'"),
    }
    k = kind.lower()
    if k not in kinds:
        return {"success": False, "error": f"kind must be one of {sorted(kinds)}"}
    command_id, hint = kinds[k]

    client = _client_instance()
    try:
        await client.connect()
        if bar is not None:
            await _position_caret(client, bar, staff)  # leaves the caret active
        resp = await client.send(command_id)
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}

    where = f"at bar {bar}" if bar is not None else "at the current selection"
    return {
        "success": bool(resp.ok),
        "opened": bool(resp.ok),
        "kind": k,
        "command": command_id,
        "code": resp.code,
        "experimental": bar is not None,
        "instruction": (
            f"The {k} popover is now open in Dorico {where}. Type {hint}, then press "
            "Enter to confirm (or Esc to cancel)."
        ),
        "caveat": _KOK_CAVEAT,
    }


# -------------------------------------------------------------- generic escape
@mcp.tool()
async def run_command(command_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run any catalogued Dorico command by id — the escape hatch for the long tail.

    Args:
        command_id: e.g. "Edit.SelectAll" or "NoteInput.SetArticulation".
        params: optional keyword arguments for the command (names as in the
            catalog). Pass ``{"confirm": true, …}`` to authorise a command the
            catalog marks *destructive*.

    The result reports the command's registry ``status`` alongside Dorico's
    response. Discover valid ids/params via the ``dorico://commands`` resource.
    """
    args = dict(params or {})
    confirm = bool(args.pop("confirm", False))
    return await _run(command_id, confirm=confirm, **args)


# ----------------------------------------------------------- score composition
@mcp.tool()
def score_schema() -> dict[str, Any]:
    """Return the ScoreSpec input format write_score/render_to_dorico expect.

    Call this instead of guessing (or reading source): it returns a copyable
    minimal example in both the flat and nested forms, the allowed enum values
    (durations, articulations, dynamics, clefs) and the indexing rules. Unknown
    keys are rejected by the parser, so match this shape exactly.
    """
    return {"success": True, **spec_schema()}


@mcp.tool()
async def write_score(score: dict, method: str = "caret", preflight: bool = True) -> dict[str, Any]:
    """Write a whole ScoreSpec into Dorico — the headline composition tool.

    Args:
        score: a ScoreSpec dict (see the schema; nested parts/staves/voices/events
            or the flat ``part.events`` shortcut). Invalid specs send nothing.
        method: "caret" (default) drives the live caret path — GUARANTEED zero
            manual steps, built only on verified commands, but key/time/clef/named
            dynamics are dropped with a warning (popover-only). "musicxml" exports
            a full-fidelity MusicXML file and imports it (richer, but the import may
            hit a file-picker dialog).
        preflight: when true, run offline range + voice-leading checks first. These
            are advisory only — they never block the write.

    Returns ``{success, method, report, analysis, warnings, caveat}``. ``kOK`` from
    Dorico means accepted, not that the notes landed — verify in the score.
    """
    spec, err = _load_spec(score)
    if err is not None:
        return err
    assert spec is not None
    if total_events(spec) == 0:
        return _zero_notes_error()

    analysis: dict[str, Any] = {}
    if preflight:
        analysis = {
            "ranges": theory.check_ranges(spec),
            "voice_leading": theory.check_voice_leading(spec, key=spec.key),
        }

    m = method.lower()
    client = _client_instance()
    if m == "caret":
        try:
            await client.connect()
            report = await render.render_score(client, spec)
        except DoricoConnectionError as e:
            return {"success": False, "error": str(e)}
        return {
            "success": report.ok,
            "method": "caret",
            "report": _report_dict(report),
            "analysis": analysis,
            "warnings": report.warnings,
            "caveat": _KOK_CAVEAT,
        }
    if m == "musicxml":
        # Full-fidelity path: write the file (key/dynamics/clefs survive here), then
        # import it over the Remote API. The import works when the path is passed raw
        # (see render.import_musicxml); it creates a NEW flow and may pop a "create a
        # new player?" dialog the user confirms in Dorico — that is Dorico's own
        # import flow. For an in-place write into the existing sheet, use 'caret'.
        try:
            written = musicxml.score_to_musicxml(spec, _temp_musicxml_path())
        except Exception as e:  # noqa: BLE001 - music21 write can fail many ways
            return {"success": False, "error": f"could not write MusicXML: {e}"}
        try:
            await client.connect()
            imported = await render.import_musicxml(client, written)
        except DoricoConnectionError as e:
            return {"success": False, "error": str(e), "exported": written}
        return {
            "success": bool(imported.get("success")),
            "method": "musicxml",
            "exported": written,
            "imported": imported,
            "analysis": analysis,
            "warnings": [imported.get("note", "")],
            "caveat": _KOK_CAVEAT,
        }
    return {"success": False, "error": f"unknown method {method!r}; use 'caret' or 'musicxml'"}


@mcp.tool()
async def render_to_dorico(score: dict, dry_run: bool = False) -> dict[str, Any]:
    """Render a ScoreSpec through the live caret path explicitly.

    ``dry_run=True`` plans the full command list and returns it **without sending
    anything** — the preview a composer approves before committing. ``dry_run=False``
    executes the plan inside a note-input session (the caret is always exited, even
    on error). Popover-only elements are dropped with warnings; ``kOK`` != effect.
    """
    spec, err = _load_spec(score)
    if err is not None:
        return err
    assert spec is not None
    if total_events(spec) == 0:
        return _zero_notes_error()

    client = _client_instance()
    if dry_run:
        commands, _ = render.plan_flow(spec)
        report = await render.render_score(client, spec, dry_run=True)
        return {
            "success": True,
            "dry_run": True,
            "commands": commands,
            "report": _report_dict(report),
            "warnings": report.warnings,
            "caveat": _KOK_CAVEAT,
        }
    try:
        await client.connect()
        report = await render.render_score(client, spec)
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}
    return {
        "success": report.ok,
        "report": _report_dict(report),
        "warnings": report.warnings,
        "caveat": _KOK_CAVEAT,
    }


@mcp.tool()
def export_musicxml(score: dict, path: str | None = None) -> dict[str, Any]:
    """Write a ScoreSpec to a MusicXML file — offline, full fidelity, no Dorico.

    Carries the key, time signature, clefs, named dynamics and tempo the live caret
    path cannot enter. ``path`` defaults to a temp file. Returns ``{success, path}``.
    """
    spec, err = _load_spec(score)
    if err is not None:
        return err
    assert spec is not None
    out = path or _temp_musicxml_path()
    try:
        written = musicxml.score_to_musicxml(spec, out)
    except Exception as e:  # noqa: BLE001 - music21 write can fail many ways
        return {"success": False, "error": f"could not write MusicXML: {e}"}
    return {"success": True, "path": written}


@mcp.tool()
def read_score(path: str, bars: str | None = None) -> dict[str, Any]:
    """Read an existing MusicXML file bar by bar — the read counterpart to write_score.

    Dorico's Remote API cannot read arbitrary bars (reads are selection-only) and
    has no export command, so to inspect a piece the user exports it once
    (File > Export > MusicXML) and this reads that file with music21 — full content,
    no Dorico contact. ``bars`` optionally limits the output: a single bar (``"8"``),
    a range (``"8-12"``) or a comma list (``"8,10,12"``); omit it to read the whole
    score. Returns ``{success, key, time_signature, tempo_bpm, part_count,
    measure_count, parts, …}`` where each part lists the requested measures with
    their notes (pitches, durations, beats).
    """
    try:
        content = musicxml.read_score(path, bars)
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}
    except ValueError as e:
        return {"success": False, "error": f"invalid bars selector {bars!r}: {e}"}
    return {"success": True, **content}


@mcp.tool()
async def import_musicxml(path: str) -> dict[str, Any]:
    """Import a MusicXML file into Dorico over the Remote API.

    Pass the path raw with forward slashes (Dorico does not URL-decode the
    ``File=`` value; a path containing ``& ? #`` is rejected up front). Dorico runs
    its normal import: it creates a **new flow** and may pop a "create a new
    player?" dialog the user must confirm in Dorico — hence ``requires_confirmation``.
    To add music into an *existing* sheet instead, use ``write_score(method="caret")``.
    Returns ``{success, supported, attempted, requires_confirmation, code, path,
    note}``.
    """
    client = _client_instance()
    try:
        await client.connect()
        return await render.import_musicxml(client, path)
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------- offline music theory
@mcp.tool()
def analyze_harmony(score: dict) -> dict[str, Any]:
    """Analyse a ScoreSpec's harmony offline (no Dorico connection).

    Returns ``{success, key, roman}``: ``key`` is a Krumhansl-Schmuckler key
    estimate (``{key, tonic, mode, confidence, alternatives}``); ``roman`` is the
    per-sonority Roman-numeral analysis in the spec's own ``key`` when set, else the
    detected key (``[]`` when no key can be established).
    """
    spec, err = _load_spec(score)
    if err is not None:
        return err
    assert spec is not None
    key_info = theory.detect_key(spec)
    key_name = spec.key or key_info.get("key")
    roman = theory.roman_numeral_analysis(spec, key_name) if key_name else []
    return {"success": True, "key": key_info, "roman": roman}


@mcp.tool()
def check_voice_leading(score: dict) -> dict[str, Any]:
    """Check a ScoreSpec's voice leading offline (no Dorico connection).

    Returns ``{success, issues, ok}`` where ``issues`` lists parallels, hidden
    perfects, voice-crossing/overlap, oversized leaps and unresolved leading tones
    (each with ``severity``/``rule``/``message``/``location``); ``ok`` is ``True``
    when there are none.
    """
    spec, err = _load_spec(score)
    if err is not None:
        return err
    assert spec is not None
    issues = theory.check_voice_leading(spec, key=spec.key)
    return {"success": True, "issues": issues, "ok": not issues}


@mcp.tool()
def suggest_next_chord(key: str, progression: list[str]) -> dict[str, Any]:
    """Suggest plausible next chords for a Roman-numeral progression (offline).

    ``key`` is e.g. ``"C major"``; ``progression`` is prior Roman numerals like
    ``["I", "vi", "ii"]`` (empty for an opening). Returns ``{success, suggestions}``
    where each suggestion is ``{roman, reason, cadential}``.
    """
    try:
        suggestions = theory.suggest_next_chord(key, progression)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "suggestions": suggestions}


@mcp.tool()
def instrument_range(instrument: str, pitch: str | None = None) -> dict[str, Any]:
    """Report an instrument's range or test a pitch against it (offline).

    With ``pitch`` given, returns ``{success, in_range}``; otherwise returns
    ``{success, lowest, highest}`` (scientific pitch names). Unknown instruments or
    unparseable pitches yield ``{success: False, error: …}``.
    """
    try:
        if pitch is not None:
            return {"success": True, "in_range": theory.note_in_range(instrument, pitch)}
        low, high = theory.instrument_bounds(instrument)
        return {"success": True, "lowest": low.nameWithOctave, "highest": high.nameWithOctave}
    except ValueError as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def check_counterpoint(
    cantus_firmus: list[str], counterpoint: list[str], species: int = 1
) -> dict[str, Any]:
    """Check first-species counterpoint of two lines offline (no Dorico).

    ``cantus_firmus`` and ``counterpoint`` are equal-length lists of scientific
    pitch names. Returns ``{success, issues, ok}``; ``species`` other than 1 yields
    ``{success: False, error: …}`` (not yet implemented).
    """
    try:
        issues = theory.check_species_counterpoint(
            cantus_firmus, counterpoint, species=species
        )
    except NotImplementedError as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "issues": issues, "ok": not issues}


# ------------------------------------------------------------- catalog discovery
@mcp.tool()
def search_commands(
    query: str = "", category: str | None = None, status: str | None = None, limit: int = 40
) -> dict[str, Any]:
    """Search the Dorico command catalog — no Dorico connection needed.

    The discovery companion to ``run_command``: instead of guessing ids or reading
    the source, ask here which commands exist and which are trustworthy. ``query``
    is a case-insensitive substring matched against each command id and its doc;
    ``category`` filters by family (e.g. ``NoteInput``, ``Edit``, ``Play``);
    ``status`` filters by integration status (``verified`` | ``broken`` |
    ``untested``). Results are ordered verified-first. Returns ``{success, total,
    count, status_counts, categories, commands}`` with at most ``limit`` entries
    (each: id, category, status, params, doc). If ``count < total``, narrow the
    query/filters to see the rest.
    """
    try:
        registry = default_registry()
    except OSError as e:
        return {"success": False, "error": f"could not load the command catalog: {e}"}

    specs = list(registry.all())
    if category is not None:
        cat = category.lower()
        specs = [s for s in specs if s.category.lower() == cat]
    if status is not None:
        st = status.lower()
        specs = [s for s in specs if s.status.value.lower() == st]
    if query:
        q = query.lower()
        specs = [s for s in specs if q in s.id.lower() or (s.doc and q in s.doc.lower())]

    rank = {CmdStatus.VERIFIED: 0, CmdStatus.BROKEN: 1, CmdStatus.UNTESTED: 2}
    specs.sort(key=lambda s: (rank.get(s.status, 3), s.id))

    return {
        "success": True,
        "total": len(specs),
        "count": min(len(specs), max(limit, 0)),
        "status_counts": registry.status_counts(),
        "categories": sorted({s.category for s in registry.all()}),
        "commands": [_spec_payload(s) for s in specs[:limit]],
    }


# ----------------------------------------------------------------- resources
@mcp.resource("dorico://commands")
def commands_catalog() -> str:
    """The full Dorico command catalog (id, category, status, params, doc).

    Lets an assistant discover the ~340 commands without a tool definition for
    each. Read ``dorico://commands/{selector}`` to filter by category
    (e.g. ``NoteInput``) or by status (``verified`` | ``broken`` | ``untested``).
    """
    try:
        registry = default_registry()
    except OSError as e:
        return json.dumps({"error": f"could not load the command catalog: {e}"})
    specs = registry.all()
    payload = {
        "count": len(specs),
        "status_counts": registry.status_counts(),
        "filters": {
            "categories": sorted({s.category for s in specs}),
            "statuses": [s.value for s in CmdStatus],
        },
        "commands": [_spec_payload(s) for s in specs],
    }
    return json.dumps(payload, indent=2)


@mcp.resource("dorico://commands/{selector}")
def commands_filtered(selector: str) -> str:
    """The catalog filtered by a category name or an integration status."""
    try:
        registry = default_registry()
    except OSError as e:
        return json.dumps({"error": f"could not load the command catalog: {e}"})
    statuses = {s.value for s in CmdStatus}
    if selector in statuses:
        specs = [s for s in registry.all() if s.status.value == selector]
        applied = {"status": selector}
    else:
        specs = registry.by_category(selector)
        applied = {"category": selector}
    payload = {
        "filter": applied,
        "count": len(specs),
        "commands": [_spec_payload(s) for s in specs],
    }
    return json.dumps(payload, indent=2)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
