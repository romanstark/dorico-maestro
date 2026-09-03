"""FastMCP server exposing Dorico Maestro's high-level musical tools.

This module provides the MCP interface described in docs/architecture.md:
intent-focused tools (add notes, transpose, playback), a generic run_command
escape hatch, and a dorico://commands resource enabling discovery of the
348 catalogued commands without per-command tool declarations.

Single-command tools route through dorico_maestro.executor.execute against the
shared default registry. Caret and note input tools drive NoteInputSession or
the client directly. Whole-score tools dispatch via dorico_maestro.render, while
offline theory tools execute locally without connecting to Dorico.

Response codes of kOK indicate command acceptance by Dorico's UI queue rather
than guaranteed score mutation. Tool responses include status codes and
verification flags where available.
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
from dorico_maestro.project_file import read_project_info as _read_project_info
from dorico_maestro.registry import default_registry
from dorico_maestro.session import NoteInputSession
from dorico_maestro.spec import CommandSpec
from dorico_maestro.toolargs import ScoreIn, score_dict

logging.basicConfig(level=logging.INFO)  # Log to stderr; stdout serves MCP stdio

_KOK_CAVEAT = (
    "'kOK' means Dorico accepted the command, not that the musical effect "
    "happened. Verify via get_status, playback, or the score."
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
        "necessarily effective: verify. Discover the full command set via the "
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
    """Render an executor :class:`Result` as a plain ``{success, …}`` dict."""
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


def _load_spec(
    score: ScoreIn | dict[str, Any],
) -> tuple[ScoreSpec | None, dict[str, Any] | None]:
    """Parse a score specification into a ScoreSpec instance.

    Returns (spec, None) on success or (None, error_dict) when validation fails.
    Invalid specifications are rejected before dispatching commands to Dorico.
    """
    try:
        return score_from_dict(score_dict(score)), None
    except ScoreSpecError as e:
        return None, {"success": False, "error": str(e)}


def _zero_notes_error() -> dict[str, Any]:
    """Return error payload for a score specification containing zero events."""
    return {
        "success": False,
        "error": (
            "Parsed OK but the score has 0 notes: your events are probably in the "
            "wrong place. A part needs either a flat 'events' list or nested "
            "'staves' -> 'voices' -> 'events'. Call score_schema for the exact shape."
        ),
        "example": spec_schema()["minimal_flat"],
    }


async def _position_caret(client: Any, bar: int, staff: int) -> None:
    """Deterministically position the caret at target bar and staff.

    Enters note input if not currently active, then rewinds to bar 1 of the top
    staff before stepping forward to the requested bar and staff index.
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
    """Serialize a RenderReport as a dictionary."""
    return {
        "ok": report.ok,
        "parts_rendered": report.parts_rendered,
        "commands_planned": report.commands_planned,
        "commands_sent": report.commands_sent,
        "warnings": report.warnings,
        "experimental": report.experimental,
    }


def _temp_musicxml_path() -> str:
    """Create and return the filesystem path to a temporary MusicXML file."""
    fd, path = tempfile.mkstemp(suffix=".musicxml")
    os.close(fd)
    return path


def _spec_payload(spec: CommandSpec) -> dict[str, Any]:
    """Serialize a CommandSpec dictionary for dorico://commands resource."""
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
    """Connect to Dorico Remote Control and return application status.

    On first connection, Dorico prompts for authorization. Accepted sessions
    persist the token in AppData for automatic authentication on subsequent runs.
    """
    try:
        client = _client_instance()
        await client.connect()
        return {"success": True, "status": await client.status()}
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_status() -> dict[str, Any]:
    """Return Dorico's current pushed application status snapshot.

    Reads are selection-based and reflect merged status deltas pushed by Dorico
    (active mode, note input state, selection flags, undo availability).
    """
    try:
        client = _client_instance()
        return {"success": True, "status": await client.status()}
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def read_selection() -> dict[str, Any]:
    """Read rhythmic and notation properties of the active selection.

    Inspects duration, dots, articulations, accidental, and event type.
    Pitch and measure/beat positions are not exposed by Dorico's Remote API.
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
def _input_mode_note(mode: object) -> str | None:
    """Format an advisory note regarding Dorico's active note-input mode.

    In kOverwrite mode, new notes replace existing bar content without read-back.
    Tested against Dorico Elements 6.2.30.
    """
    if mode == "kOverwrite":
        return (
            "OVERWRITE mode: whatever stood in those bars is gone, and this "
            "interface cannot read a bar to say what it was. Set Dorico to Insert "
            "before writing into bars that are not empty."
        )
    if isinstance(mode, str) and mode:
        return f"Note-input mode was {mode}."
    return None


@mcp.tool()
async def add_notes(
    notes: list[str],
    duration: str = "quarter",
    as_chord: bool = False,
) -> dict[str, Any]:
    """Input notes at the caret, then leave note-input mode cleanly.

    ONE insertion at the current caret. For a SEQUENCE of notes or chords over
    time, use ``write_score`` / ``render_to_dorico`` with a ScoreSpec (a chord is
    one event with >=2 pitches); repeated ``add_notes`` calls do NOT chain: each
    re-enters note input at the same spot, so successive chords stack on one beat.

    Args:
        notes: pitches like ``["C4", "E4", "G4"]`` (letter + optional #/b + octave).
        duration: whole | half | quarter | eighth | sixteenth | 32nd | 64th.
        as_chord: if true, stack the notes as one chord instead of a sequence.

    Uses :class:`NoteInputSession`, so note input is always exited even on error.
    Success indicates command acceptance (kOK); verify note placement via
    get_status, playback, or score inspection.
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
        # The mode BEFORE the write: that is the one the notes landed in.
        mode_before = (await client.status()).get("noteInputMode")
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
            "note_input_mode": mode_before,
            "displaces_existing": mode_before == "kOverwrite",
            "mode_note": _input_mode_note(mode_before),
            "note": _KOK_CAVEAT,
        }
    except ValueError as e:  # malformed pitch string: caret already exited
        return {"success": False, "error": str(e), "notes": notes}
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def add_rest(duration: str = "quarter") -> dict[str, Any]:
    """Input a rest of the specified duration at the active caret position.

    Args:
        duration: Duration name (whole, half, quarter, eighth, sixteenth, 32nd, 64th).

    Returns:
        Result dictionary indicating success, duration, undo flag, and mode.
    """
    try:
        dur = _parse_duration(duration)
    except ValueError:
        return _duration_error(duration)

    client = _client_instance()
    try:
        await client.connect()
        # The mode BEFORE the write: that is the one the notes landed in.
        mode_before = (await client.status()).get("noteInputMode")
        async with NoteInputSession(client) as session:
            await session.set_duration(dur)
            await session.rest()
        status = await client.status()
        return {
            "success": True,
            "duration": dur.value,
            "can_undo": status.get("canUndo"),
            "note_input_mode": mode_before,
            "displaces_existing": mode_before == "kOverwrite",
            "mode_note": _input_mode_note(mode_before),
            "verified": False,
            "note": (
                "UNVERIFIED: rest entry sends NoteInput.RestMode, which is a toggle: a "
                "single send may only arm rest input rather than place a rest. "
                "Confirm in the score. " + _KOK_CAVEAT
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
    make a selection first. Maps to ``NoteEdit.Pitch{Up,Down}[Chromatic|Octave]``,
    all of which are catalogued. The result carries that row's ``registry_status``,
    so read it before trusting the transposition, and check the score.
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
    """Report time signature limitation and suggest supported alternatives.

    Dorico requires time signatures to be entered via an interactive popover,
    which cannot receive text parameters over the Remote API.

    Use open_popover(kind="time") to open the popover interactively, or use
    export_musicxml / write_score(method="musicxml") for direct file-based setting.
    """
    return {
        "success": False,
        "supported": False,
        "requested": signature,
        "error": (
            "Setting a specific time signature over the API is not supported directly: "
            "Dorico enters time signatures via a popover that cannot be filled remotely. Use "
            f"open_popover(kind='time') and type {signature!r} in Dorico, or use the "
            "MusicXML path (write_score(method='musicxml') or export_musicxml), which "
            "defines the time signature in the score file."
        ),
    }


@mcp.tool()
async def set_key_signature(key: str = "C major") -> dict[str, Any]:
    """Report key signature limitation and suggest supported alternatives.

    Dorico requires key signatures to be entered via an interactive popover,
    which cannot receive text parameters over the Remote API.

    Use open_popover(kind="key") to open the popover interactively, or use
    export_musicxml / write_score(method="musicxml") for direct file-based setting.
    """
    return {
        "success": False,
        "supported": False,
        "requested": key,
        "error": (
            "Setting a specific key signature over the API is not supported directly: "
            "Dorico enters key signatures via a popover that cannot be filled remotely. Use "
            f"open_popover(kind='key') and type {key!r} in Dorico, or use the "
            "MusicXML path (write_score(method='musicxml') or export_musicxml), which "
            "defines the key signature in the score file."
        ),
    }


@mcp.tool()
async def navigate(target: str, bar: int | None = None) -> dict[str, Any]:
    """Scroll the score viewport to start, end, or report bar navigation.

    Args:
        target: Viewport target ("start" or "end"). Target "bar" redirects to goto_bar.
        bar: 1-indexed bar number (used only when target="bar").

    Note:
        target="start" and "end" move the visual viewport (View.MoveViewportTo*).
        To reposition the caret for note entry, use goto_bar(bar, staff, beat).
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
                "navigate cannot jump to a bar: Edit.GoTo opens Dorico's Go To "
                f"dialog which cannot be filled remotely. Call goto_bar(bar={bar}) "
                "instead: it positions the caret via note-input navigation commands "
                "and leaves note input active."
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
    """Switch Dorico's workspace window mode.

    Args:
        mode: Target mode name ("write", "engrave", "play", "print", or "setup").
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
    """Control Dorico playback transport.

    Args:
        action: Transport action ("play", "stop", or "rewind").
        location: Playhead origin for play ("kPlayhead", "kSelection", "kStartOfFlow",
            or "kLastStartPosition").
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
    """Save the current project (File.Save)."""
    return await _run("File.Save")


@mcp.tool()
async def export_pdf(all_layouts: bool = False) -> dict[str, Any]:
    """Export the score to PDF unattended without opening a dialog.

    Sends Print.ExportCurrentLayoutAsPDF (or Print.ExportAllLayoutsAsPDF).
    The file is written adjacent to the .dorico project file using the layout name.

    Note:
        Receiving kOK does not prove a file exists yet: Dorico executes commands
        on its UI thread, so requests sent while a modal dialog is open queue
        until the dialog closes. Tested against Dorico Elements 6.2.30.
    """
    return await _run(
        "Print.ExportAllLayoutsAsPDF" if all_layouts else "Print.ExportCurrentLayoutAsPDF"
    )


@mcp.tool()
async def goto_bar(bar: int, staff: int = 0, beat: float = 1.0) -> dict[str, Any]:
    """Move the caret to a bar and return the assumed position.

    Dorico does not expose caret coordinates over the Remote API. This tool
    dead-reckons position by entering note input, rewinding to bar 1 of the top
    staff, and stepping forward to the specified bar, staff, and beat.
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
            "Position is dead-reckoned, not read from Dorico: correct as long as no "
            "manual edit moved the caret since. Beat is snapped to the rhythmic grid."
        ),
    }


@mcp.tool()
async def open_popover(kind: str, bar: int | None = None, staff: int = 0) -> dict[str, Any]:
    """Open an input popover (dynamic, tempo, key, time, or clef) at the caret.

    The Remote Control API can open popovers but cannot populate their input
    fields remotely. This tool positions the caret if requested, triggers the
    corresponding NoteInput.Create* command, and returns instructions for manual
    input in Dorico.
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
    """Dispatch any catalogued Dorico command ID with optional parameters.

    Args:
        command_id: Exact command string as declared in the catalog.
        params: Optional dictionary of query parameters. Set {"confirm": True}
            to authorize destructive commands.
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
async def write_score(
    score: ScoreIn, method: str = "caret", preflight: bool = True
) -> dict[str, Any]:
    """Render a ScoreSpec into Dorico via caret input or MusicXML import.

    Args:
        score: Score specification matching ScoreSpec schema.
        method: Render mechanism: "caret" (default live caret entry) or "musicxml"
            (full score export and import into a new flow).
        preflight: If True, execute offline range and voice leading analysis
            before dispatching commands.

    Returns:
        Result dictionary containing execution report, warnings, and caveats.
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
async def render_to_dorico(score: ScoreIn, dry_run: bool = False) -> dict[str, Any]:
    """Render a ScoreSpec through the live caret path.

    Args:
        score: Score specification matching ScoreSpec schema.
        dry_run: If True, plan commands and return execution report without dispatching.

    Returns:
        Result dictionary containing command plan or execution outcome.
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
def export_musicxml(score: ScoreIn, path: str | None = None) -> dict[str, Any]:
    """Write a ScoreSpec to a MusicXML file offline.

    Preserves key, time signature, clefs, dynamics, and tempo attributes.
    If path is omitted, writes to a temporary file.
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
def read_project_info(path: str) -> dict[str, Any]:
    """Read metadata, flows, and player rosters from a saved .dorico file.

    Extracts document and per-flow metadata directly from the project ZIP archive
    without requiring a running Dorico instance.
    """
    return _read_project_info(path)


@mcp.tool()
def read_score(path: str, bars: str | None = None) -> dict[str, Any]:
    """Read an existing MusicXML file measure by measure using music21.

    Args:
        path: Filesystem path to the MusicXML score.
        bars: Optional measure filter string, e.g. "8", "8-12", or "8,10,12".
            If omitted, reads the entire score.

    Returns:
        Structured dictionary containing metadata, parts, measures, and note events.
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
    """Import a MusicXML file into Dorico via the Remote Control API.

    Dispatches File.Open with MusicXMLImportFilter. Imports create a new flow
    and may display player assignment confirmation prompts. To add music into an
    existing sheet instead, use write_score(method="caret").

    Args:
        path: Filesystem path to the MusicXML file.

    Returns:
        Dict with success, supported, attempted, requires_confirmation, code, path,
        and note.
    """
    client = _client_instance()
    try:
        await client.connect()
        return await render.import_musicxml(client, path)
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------- offline music theory
@mcp.tool()
def analyze_harmony(score: ScoreIn) -> dict[str, Any]:
    """Analyze harmonic structure and key estimation offline using music21.

    Returns key estimation details and Roman numeral sonority analyses.
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
def check_voice_leading(score: ScoreIn) -> dict[str, Any]:
    """Audit voice leading rules offline (parallels, overlaps, spacing).

    Returns a structured list of detected voice leading issues.
    """
    spec, err = _load_spec(score)
    if err is not None:
        return err
    assert spec is not None
    issues = theory.check_voice_leading(spec, key=spec.key)
    return {"success": True, "issues": issues, "ok": not issues}


@mcp.tool()
def suggest_next_chord(key: str, progression: list[str]) -> dict[str, Any]:
    """Suggest functional harmonic continuations for a Roman numeral progression.

    Args:
        key: Tonal center, e.g. "C major" or "A minor".
        progression: Prior Roman numeral chords, e.g. ["I", "vi", "ii"].
    """
    try:
        suggestions = theory.suggest_next_chord(key, progression)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "suggestions": suggestions}


@mcp.tool()
def instrument_range(instrument: str, pitch: str | None = None) -> dict[str, Any]:
    """Query standard instrument ranges or validate pitch playability offline.

    Args:
        instrument: Instrument name, e.g. "violin", "flute", "cello".
        pitch: Optional scientific pitch name to test, e.g. "C4".
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
    """Analyze two-part first-species counterpoint against standard rules.

    Args:
        cantus_firmus: List of scientific pitch names.
        counterpoint: List of scientific pitch names.
        species: Counterpoint species (default: 1).
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
    """Search the Dorico command catalog by query, category, or status.

    Args:
        query: Case-insensitive substring matched against command IDs and docs.
        category: Filter by command family, e.g. "NoteInput", "Edit", "Play".
        status: Filter by integration status (verified, reachable, unavailable,
            broken, untested).
        limit: Maximum number of command entries to return.

    Returns:
        Dictionary containing total matches, status counts, and matching commands.
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

    rank = {
        CmdStatus.VERIFIED: 0,
        CmdStatus.REACHABLE: 1,
        CmdStatus.BROKEN: 2,
        CmdStatus.UNAVAILABLE: 3,
        CmdStatus.UNTESTED: 4,
    }
    specs.sort(key=lambda s: (rank.get(s.status, len(rank)), s.id))

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
    """Return the complete Dorico command catalog formatted as JSON.

    Exposes all 348 commands with metadata, parameter signatures, and verification
    status without requiring individual tool declarations.
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
    """Return commands matching a category name or integration status as JSON."""
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
