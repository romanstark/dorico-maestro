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

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

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
from dorico_maestro.toolargs import (
    BarArg,
    DurationArg,
    PickupArg,
    ScoreArg,
    ScoreIn,
    StaffArg,
    score_dict,
)

logging.basicConfig(level=logging.INFO)  # Log to stderr, stdout serves MCP stdio

_KOK_CAVEAT = (
    "'kOK' means Dorico accepted the command, not that the musical effect "
    "happened. Verify via get_status, playback, or the score."
)

# can_undo indicates an undo action is available. Entering note input alone pushes
# an undo record, so this flag confirms undo availability rather than verified placement.
_CAN_UNDO_CAVEAT = (
    "can_undo indicates an undo action is available in Dorico. Entering note input "
    "alone sets this flag, so it does not verify whether notes were placed."
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


# --------------------------------------------------------------------------- #
# Tool annotations: the machine-readable half of behavioural disclosure.
# --------------------------------------------------------------------------- #
# A client reads these before it reads a word of prose. ``idempotentHint`` is the
# one that carries risk, because it says a failed call is safe to send again, and
# here a call that failed may still have been accepted: kOK confirms only that
# Dorico's UI queue took the command (docs/protocol.md, 'Command Acceptance vs
# Effect'). So it is true only where sending the same arguments twice cannot leave
# a different score than sending them once. Note entry, transposition and anything
# that advances the caret declare false.
#
# ``openWorldHint`` is false throughout: the domain is one running copy of Dorico
# and the files it reads and writes.

#: Reads Dorico or a file on disk and changes nothing.
READS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

#: Puts the application into a named state, which is the same state twice over.
SETS = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

#: Writes music, moves the caret, or otherwise builds on what is already there.
ADDS = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)

#: Overwrites a file or discards something the caller cannot get back.
DESTROYS = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)


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
        "Reads are selection-only, use get_status for state. 'kOK' means accepted, not "
        "necessarily effective: verify. Discover the full command set via the "
        "dorico://commands resource, run anything with run_command. See docs/protocol.md."
    ),
)


# ------------------------------------------------------------------------ helpers
def _parse_duration(duration: str) -> NoteDuration:
    return NoteDuration(duration.lower())


def _duration_error(duration: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": f"invalid duration {duration!r}, use one of {[d.value for d in NoteDuration]}",
    }


def _result_dict(
    result: Result, registry_status: CmdStatus | None = None, **extra: Any
) -> dict[str, Any]:
    """Render an executor :class:`Result` as a plain ``{success, …}`` dict."""
    out: dict[str, Any] = {
        "success": result.ok and not result.blocked,
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
            "Score specification contains 0 notes. A part must define either a "
            "flat 'events' list or nested 'staves' -> 'voices' -> 'events'. "
            "Call score_schema for the exact structure."
        ),
        "example": spec_schema()["minimal_flat"],
    }


async def _position_caret(client: Any, bar: int, staff: int, *, pickup: bool = False) -> None:
    """Deterministically position the caret at target bar and staff.

    Jumps to the start of the flow with render.CARET_TO_FLOW_START, then steps
    forward to the requested bar and down to the requested staff. The jump costs
    the same four commands at any flow length, so no caller has to know how long
    the flow is.

    Bar navigation counts bars rather than bar numbers, and Dorico does not count
    a pickup bar as bar 1, so navigation steps from flow start must account for
    pickup presence to match printed measure numbers. Pushed status reveals
    nothing about a pickup, requiring explicit pickup indication (Dorico Elements 6.2.30).
    """
    for command in render.CARET_TO_FLOW_START:
        await client.send(command)
    for _ in range(max(bar - (0 if pickup else 1), 0)):
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
@mcp.tool(annotations=READS)
async def connect_to_dorico() -> dict[str, Any]:
    """Connect to Dorico Remote Control and return application status.

    Call this before anything else here: every other tool that talks to Dorico
    needs the session this opens. It is safe to call again on an open connection.

    Returns:
        Result dictionary with the connection outcome and the first status snapshot.

    Note:
        On the first connection Dorico shows an authorization prompt inside the
        application, and nothing proceeds until a person accepts it. The token is
        then kept in AppData and later runs connect without asking.

        Use get_status afterwards to re-read the state without reconnecting.
    """
    try:
        client = _client_instance()
        await client.connect()
        return {"success": True, "status": await client.status()}
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


@mcp.tool(annotations=READS)
async def get_status() -> dict[str, Any]:
    """Return Dorico's current pushed application status snapshot.

    The state of the application rather than of the music: which mode is active,
    whether note input is running, whether anything is selected, whether an undo is
    available.

    Returns:
        Result dictionary carrying the merged status snapshot.

    Note:
        Dorico pushes status as deltas and this is the accumulated snapshot of them,
        so it costs nothing to read and needs no command to be sent.

        This is also how to check whether a command that answered kOK actually took
        effect, since kOK only says the UI queue accepted it (docs/protocol.md,
        'Command Acceptance vs Effect'). For the properties of the selected notes
        rather than the state of the application, use read_selection.
    """
    try:
        client = _client_instance()
        return {"success": True, "status": await client.status()}
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


@mcp.tool(annotations=READS)
async def read_selection() -> dict[str, Any]:
    """Read rhythmic and notation properties of the active selection.

    Reports duration, dots, articulations, accidental and event type for whatever a
    person has selected in Dorico. Nothing here can change the selection, so make it
    in Dorico or move the caret with goto_bar first.

    Returns:
        Result dictionary with has_selection, and the properties when it is true.
        An empty selection answers has_selection=False rather than an error.

    Note:
        Pitch and bar or beat position are not exposed by Dorico's Remote API, so
        they are absent here and no amount of selecting will produce them. To read
        pitches, export the flow with export_musicxml and read it with read_score,
        or read a saved project with read_project_info.
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


@mcp.tool(annotations=ADDS)
async def add_notes(
    notes: Annotated[
        list[str],
        Field(
            description=(
                "Pitches in scientific notation, e.g. ['C4', 'E4', 'G4']: a letter, "
                "an optional # or b, then the octave number, where C4 is middle C."
            )
        ),
    ],
    duration: DurationArg = "quarter",
    as_chord: Annotated[
        bool,
        Field(
            description=(
                "True stacks the pitches into one chord on a single beat. False "
                "enters them one after another, each of the given duration."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Input notes at the caret, then leave note-input mode cleanly.

    ONE insertion at the current caret. For a SEQUENCE of notes or chords over
    time, use ``write_score`` / ``render_to_dorico`` with a ScoreSpec (a chord is
    one event with >=2 pitches). Repeated ``add_notes`` calls do NOT chain: each
    re-enters note input at the same spot, so successive chords stack on one beat.

    Returns:
        Result dictionary with the notes and duration entered, the note input mode
        they landed in, whether an undo is available, and displaces_existing.

    Note:
        Read displaces_existing before treating this as an addition. Dorico has an
        overwrite note input mode, and in it these notes replace the music already
        at the caret instead of pushing it along. The mode belongs to the
        application rather than to this call, so it is reported back and not chosen
        here. get_status reads it beforehand.

        Uses :class:`NoteInputSession`, so note input is always exited even on
        error. Success indicates command acceptance (kOK). Verify note placement
        via get_status, playback, or score inspection. Do not read can_undo as
        that verification: entering note input alone already sets it.
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
            "verified": False,
            "note": _CAN_UNDO_CAVEAT + " " + _KOK_CAVEAT,
        }
    except ValueError as e:  # malformed pitch string: caret already exited
        return {"success": False, "error": str(e), "notes": notes}
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


@mcp.tool(annotations=ADDS)
async def add_rest(duration: DurationArg = "quarter") -> dict[str, Any]:
    """Input one rest at the caret, then leave note-input mode cleanly.

    One rest at the current caret position, which then advances by that duration.
    Note input is always exited afterwards, including when the call fails.

    Returns:
        Result dictionary with success, the duration entered, the undo flag and the
        resulting mode.

    Note:
        Success means Dorico accepted the command (kOK), not that the rest is where
        it was wanted (docs/protocol.md, 'Command Acceptance vs Effect'). Check with
        get_status or by inspecting the score.

        For a passage rather than one rest, put rest events in a ScoreSpec and use
        write_score: repeated calls here do not chain, because each one re-enters
        note input at the caret. Use goto_bar first to choose where it lands.
        Do not read can_undo as evidence: entering note input alone already sets it.
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
                "Confirm in the score. " + _CAN_UNDO_CAVEAT + " " + _KOK_CAVEAT
            ),
        }
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


# --------------------------------------------------------------------- editing
@mcp.tool(annotations=ADDS)
async def transpose(
    direction: Annotated[
        str,
        Field(
            description=(
                "Which way to shift pitch: 'up' moves higher, 'down' moves lower."
            )
        ),
    ],
    chromatic: Annotated[
        bool,
        Field(
            description=(
                "True steps by an exact chromatic semitone. False steps diatonically "
                "within the current key signature, so the interval varies by scale degree."
            )
        ),
    ] = False,
    octave: Annotated[
        bool,
        Field(
            description=(
                "True shifts by a full octave (12 semitones or 8 diatonic steps) "
                "and overrides chromatic, which is then ignored."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Transpose currently selected notes up or down in the score.

    Directly modifies the score by altering the pitches of the current selection
    in place. Relative and repeatable: calling it twice moves the selection twice
    as far, with no absolute target pitch.

    Returns:
        Result dictionary with the command outcome and the catalog row
        registry_status.

    Note:
        When to use: Shift existing selected notes by semitone, diatonic step,
        or octave intervals.
        When NOT to use: Do not use to enter new music (use write_score or
        add_notes). Do not use to change key signatures (use set_key_signature).

        Operates strictly on the active selection (reads are selection-only). If
        nothing is selected, Dorico ignores the command and no notes are modified;
        make a selection first or place the caret via goto_bar.

        Parameters: direction sets shift orientation ('up'/'down'). chromatic
        shifts by exact semitone when True or diatonically when False. octave=True
        overrides chromatic and shifts by a full octave.

        Maps to NoteEdit.Pitch{Up,Down}[Chromatic|Octave]. Read registry_status in
        the returned result and verify the change in the score or via playback.
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


@mcp.tool(annotations=READS)
async def set_time_signature(
    signature: Annotated[
        str,
        Field(
            description=(
                "The time signature that was wanted, e.g. '4/4', '3/4' or '6/8'. "
                "Reported back in the answer so the caller can carry it to one of "
                "the alternatives. Nothing is written from it."
            )
        ),
    ] = "4/4",
) -> dict[str, Any]:
    """Report that a time signature cannot be set here, and what to use instead.

    This writes nothing and always reports failure, which is the honest answer
    rather than a silent no-op: Dorico takes a time signature only through an
    interactive popover, and the Remote API cannot type into one.

    Returns:
        Result dictionary explaining the limitation and naming the alternatives.

    Note:
        Two ways round it. For a person at the keyboard, open_popover(kind='time')
        opens the popover for them to type into. For an unattended write, put the
        time signature in a ScoreSpec and use write_score(method='musicxml') or
        export_musicxml, both of which set it through the file rather than the UI.
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


@mcp.tool(annotations=READS)
async def set_key_signature(
    key: Annotated[
        str,
        Field(
            description=(
                "The key that was wanted, e.g. 'G major', 'C# minor' or "
                "'Bb major'. Reported back in the answer so the caller can carry "
                "it to one of the alternatives. Nothing is written from it."
            )
        ),
    ] = "C major",
) -> dict[str, Any]:
    """Report that a key signature cannot be set here, and what to use instead.

    This writes nothing and always reports failure, for the same reason as
    set_time_signature: Dorico takes a key signature only through an interactive
    popover, and the Remote API cannot type into one.

    Returns:
        Result dictionary explaining the limitation and naming the alternatives.

    Note:
        For a person at the keyboard, open_popover(kind='key') opens the popover for
        them to type into. For an unattended write, put the key in a ScoreSpec and
        use write_score(method='musicxml') or export_musicxml, which set it through
        the file rather than the UI.
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


@mcp.tool(annotations=SETS)
async def navigate(
    target: Annotated[
        str,
        Field(
            description=(
                "Where to scroll: 'start' for the beginning of the flow, 'end' for "
                "the end. 'bar' is accepted and redirects to goto_bar, because "
                "reaching a bar means moving the caret rather than the viewport."
            )
        ),
    ],
    bar: Annotated[
        int | None,
        Field(
            description=(
                "Bar number counted from 1, read only when target is 'bar'. "
                "Ignored for 'start' and 'end'."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Scroll the score viewport to the start or end of the flow.

    Moves what is on screen through View.MoveViewportTo*, and nothing else: the
    caret stays where it was and no music changes.

    Returns:
        Result dictionary reporting the scroll, or the redirect for target='bar'.

    Note:
        Use goto_bar instead to put the caret somewhere, which is what note entry
        needs. This tool would scroll past the bar and leave the caret behind. A
        target of 'bar' says so rather than doing half the job.
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
            "Moves the viewport only, it does not move the caret/selection.",
        )
        return result
    return {"success": False, "error": "target must be 'start', 'end', or 'bar'"}


# ----------------------------------------------------------- window & transport
@mcp.tool(annotations=SETS)
async def switch_mode(
    mode: Annotated[
        str,
        Field(
            description=(
                "Which mode to show: 'setup' for players, layouts and flows, "
                "'write' for note entry and editing, 'engrave' for graphical "
                "adjustment and spacing, 'play' for the track view and VST "
                "instruments, 'print' for print and export setup. The raw kName "
                "forms are accepted too."
            )
        ),
    ],
) -> dict[str, Any]:
    """Switch which of Dorico's five workspace modes is on screen.

    Changes the window and nothing in the music: no notes, no playback position and
    no selection move as a result.

    Returns:
        Result dictionary reporting whether the switch was accepted.

    Note:
        Note entry needs write mode, so switch there before add_notes, add_rest,
        goto_bar or write_score if the project might be in another one. get_status
        reports which mode is active without changing it.

        This is not navigation. To scroll the score use navigate, and to move the
        caret use goto_bar.
    """
    key = mode if mode in _MODE_VALUES else _MODE_ALIASES.get(mode.lower())
    if key not in _MODE_VALUES:
        return {
            "success": False,
            "error": f"unknown mode {mode!r}, choose from {sorted(_MODE_ALIASES)}",
        }
    return await _run("Window.SwitchMode", mode=key)


@mcp.tool(annotations=SETS)
async def playback(
    action: Annotated[
        str,
        Field(
            description=(
                "What the transport should do: 'play', 'stop' or 'rewind'."
            )
        ),
    ] = "play",
    location: Annotated[
        str,
        Field(
            description=(
                "Where playback starts from, read only when action is 'play': "
                "'kPlayhead' from the playhead, 'kSelection' from what is "
                "selected, 'kStartOfFlow' from the top, 'kLastStartPosition' from "
                "wherever the last play began."
            )
        ),
    ] = "kPlayhead",
) -> dict[str, Any]:
    """Start, stop or rewind Dorico playback.

    Transport only: no note, dynamic or layout changes as a result.

    Returns:
        Result dictionary reporting whether the transport command was accepted.

    Note:
        Playback is the one way to hear whether a write landed, since kOK proves
        only that the command was accepted (docs/protocol.md, 'Command Acceptance
        vs Effect'). It makes sound, which matters if a person is in the room.

        Use navigate to scroll the score without playing, and goto_bar to move the
        caret. Neither of those moves the playhead.
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


@mcp.tool(annotations=DESTROYS)
async def save() -> dict[str, Any]:
    """Save the open project over its existing file on disk.

    Sends File.Save, which writes the project in place. A project that has never
    been saved has no path to write to, so this cannot be relied on to save one:
    check that the project has a file before treating it as a save.

    Returns:
        Result dictionary reporting whether the command was accepted.

    Note:
        When to use: Save changes in an already-saved project file on disk.
        When NOT to use: Do not use if the project has never been saved (has no
        path). To produce a file for external use without altering the open
        project file, use export_pdf or export_musicxml instead.

        Acceptance is not completion here either (docs/protocol.md, "Command
        Acceptance vs Effect"), so a kOK does not prove the file on disk has changed.

        This overwrites the .dorico project file in place.
    """
    return await _run("File.Save")


@mcp.tool(annotations=ADDS)
async def export_pdf(
    all_layouts: Annotated[
        bool,
        Field(
            description=(
                "False exports the layout currently on screen through "
                "Print.ExportCurrentLayoutAsPDF. True exports every layout in the "
                "project through Print.ExportAllLayoutsAsPDF, which writes one file "
                "per layout."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Export the score to PDF unattended without opening a dialog.

    Writes the file next to the .dorico project, named after the layout. This is the
    unattended path. File.Export opens a modal dialog and is not usable from here.

    Returns:
        Result dictionary reporting whether the command was accepted.

    Note:
        Receiving kOK does not prove a file exists yet. Dorico runs commands on its
        UI thread, so a request sent while a modal dialog is open waits in the queue
        until the dialog closes. Tested against Dorico Elements 6.2.30. Check the
        expected path on disk rather than trusting the return.

        The all_layouts path is catalogued as untested, unlike the current-layout
        path which is verified, so treat a multi-layout export as unproven and check
        what actually landed. For an interchange format rather than a printable one,
        use export_musicxml.
    """
    return await _run(
        "Print.ExportAllLayoutsAsPDF" if all_layouts else "Print.ExportCurrentLayoutAsPDF"
    )


@mcp.tool(annotations=ADDS)
async def goto_bar(
    bar: BarArg,
    staff: StaffArg = 0,
    beat: Annotated[
        float,
        Field(
            description=(
                "Beat within the bar, counted from 1, so 1.0 is the downbeat and "
                "2.5 is halfway through the second beat."
            )
        ),
    ] = 1.0,
    pickup: PickupArg = False,
) -> dict[str, Any]:
    """Move the caret to a bar and return the assumed position.

    The position is assumed, not read. Dorico exposes no caret coordinates over the
    Remote API, so this dead-reckons: it enters note input, rewinds to bar 1 of the
    top staff, then steps forward to the bar, staff and beat asked for.

    Returns:
        Result dictionary with the assumed caret position and the caveat attached.

    Note:
        Because it counts rather than measures, the answer drifts from the truth if
        anything moved the caret in between, and it cannot detect that. Treat it as
        the position it aimed for, and confirm what was written with read_selection
        or by inspecting the score.

        This enters note input, so it is the call to make before add_notes or
        add_rest. To scroll the view without touching the caret, use navigate.

        Ask whether the flow opens with a pickup bar before relying on a bar
        number, because Dorico leaves a pickup out of the count and this lands one
        bar short without the pickup flag. The API cannot tell, so the person with
        the score on screen has to say, or read an exported MusicXML with
        read_score.

        The move costs the same handful of commands at any flow length, so a long
        flow is no less exact than a short one.
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
        await _position_caret(client, bar, staff, pickup=pickup)
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
        "pickup": pickup,
        "caveat": (
            "Position is dead-reckoned, not read from Dorico: correct as long as no "
            "manual edit moved the caret since. Beat is snapped to the rhythmic grid."
            + (
                ""
                if pickup
                else " Counted WITHOUT a pickup bar: if this flow starts with an "
                "upbeat, the caret is one bar short of the printed number and "
                "pickup=True is what corrects it."
            )
        ),
    }


@mcp.tool(annotations=ADDS)
async def open_popover(
    kind: Annotated[
        str,
        Field(
            description=(
                "Which popover to open: 'dynamic', 'tempo', 'key', 'time' or "
                "'clef'. Each maps to its own NoteInput.Create* command."
            )
        ),
    ],
    bar: Annotated[
        int | None,
        Field(
            description=(
                "Bar to move the caret to first, counted from 1. Omit to open the "
                "popover wherever the caret already is."
            )
        ),
    ] = None,
    staff: StaffArg = 0,
    pickup: PickupArg = False,
) -> dict[str, Any]:
    """Open an input popover at the caret for manual text entry.

    Opens the popover and stops there. Dorico's Remote API cannot populate popovers
    directly, so this positions the caret if requested, triggers the
    NoteInput.Create* command, and returns the required input format.

    Returns:
        Result dictionary with the popover kind, caret location, and typing
        instructions.

    Note:
        Requires user interaction in Dorico. Opening a popover leaves it waiting
        for keyboard input. Treat anything sent while it waits as unsafe: a modal
        dialog leaves later commands accepted but unexecuted (docs/protocol.md,
        'Modal Dialog Detection'), and a waiting popover has not been measured to
        behave any better.

        For an unattended write, put the marking in a ScoreSpec and use write_score:
        dynamics and clefs are ScoreSpec fields, and a key or time signature goes in
        through write_score(method='musicxml').
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
            # leaves the caret active
            await _position_caret(client, bar, staff, pickup=pickup)
        resp = await client.send(command_id)
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}

    where = f"at bar {bar}" if bar is not None else "at the current selection"
    return {
        "success": resp.ok,
        "opened": resp.ok,
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
@mcp.tool(annotations=ADDS)
async def run_command(
    command_id: Annotated[
        str,
        Field(
            description=(
                "The command to send, exactly as the catalog declares it, e.g. "
                "'Edit.Undo'. Case and spelling are not corrected. Find one with "
                "search_commands or the dorico://commands resource."
            )
        ),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Query parameters for the command, whose names differ per command "
                "and are declared in the catalog rather than here. Add "
                "{'confirm': True} to authorise a command the catalog marks "
                "destructive. Without it such a command is refused."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Dispatch any catalogued Dorico command by ID, with optional parameters.

    The escape hatch, reaching the whole catalogued command set rather than the
    handful of tools here. What it can send is bounded by the catalog.

    Returns:
        Result dictionary with the command outcome and the catalog row status, which
        says how well that command is actually established.

    Note:
        Read the registry_status in the answer before trusting the result: a row may
        be verified, reachable, unavailable, broken or untested, and only verified
        has been seen to work. Combined with kOK meaning acceptance rather than
        effect (docs/protocol.md), a clean return from an untested row is weak
        evidence. Check with get_status or by inspecting the score.

        Prefer a dedicated tool where one exists: write_score, transpose,
        switch_mode, playback and save all add validation or verification this does
        not. Use search_commands to find a command ID and what it takes.
    """
    args = dict(params or {})
    confirm = bool(args.pop("confirm", False))
    return await _run(command_id, confirm=confirm, **args)


# ----------------------------------------------------------- score composition
@mcp.tool(annotations=READS)
def score_schema() -> dict[str, Any]:
    """Return the ScoreSpec input format write_score/render_to_dorico expect.

    Call this instead of guessing or reading source. Unknown keys are rejected by
    the parser rather than ignored, so the shape has to match exactly.

    Returns:
        Result dictionary with a copyable minimal example in both the flat and the
        nested form, the allowed enum values (durations, articulations, dynamics,
        clefs) and the indexing rules.
    """
    return {"success": True, **spec_schema()}


@mcp.tool(annotations=ADDS)
async def write_score(
    score: ScoreArg,
    method: Annotated[
        str,
        Field(
            description=(
                "How to get the music in. 'caret' types it into the flow already "
                "open, adding to what is there. 'musicxml' writes the whole score "
                "to a file and imports it as a new flow, which is the only path "
                "that carries key and time signatures."
            )
        ),
    ] = "caret",
    preflight: Annotated[
        bool,
        Field(
            description=(
                "True runs the offline range and voice-leading checks first and "
                "reports what they found before anything is sent to Dorico. It "
                "warns rather than blocks."
            )
        ),
    ] = True,
) -> dict[str, Any]:
    """Render a ScoreSpec into Dorico by caret entry or MusicXML import.

    The main way to write music here, and the one to reach for whenever there is
    more than a single beat to enter: add_notes and add_rest do not chain, because
    each re-enters note input at the caret and stacks on the same beat.

    Returns:
        Result dictionary with the execution report, the preflight warnings, and the
        caveats attached to what was dispatched.

    Note:
        Choose the method by what the score needs. 'caret' adds to the current flow
        and leaves the rest of the project alone, but it cannot set a key or time
        signature, because those go in through a popover no API can type into.
        'musicxml' carries them, at the price of arriving as a new flow rather than
        joining the current one.

        Call score_schema first for the exact shape: unknown keys are refused rather
        than ignored. Dorico must be in write mode, so use switch_mode if unsure.
        Nothing is verified by the return, since kOK means accepted rather than
        effective (docs/protocol.md), so check the result in the score.

        The caret path repositions between staves by jumping to the start of the
        flow, so it needs to know nothing about the flow's meter or length.
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
    return {"success": False, "error": f"unknown method {method!r}, use 'caret' or 'musicxml'"}


@mcp.tool(annotations=ADDS)
async def render_to_dorico(
    score: ScoreArg,
    dry_run: Annotated[
        bool,
        Field(
            description=(
                "True plans the commands and returns them without sending any, "
                "which changes nothing in Dorico and needs no connection to it. "
                "False dispatches them."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Render a ScoreSpec through the caret path, or plan it without sending.

    The lower half of write_score, exposed on its own for the two cases where that
    matters: seeing the command plan before it runs, and skipping the preflight
    checks and the MusicXML option that write_score adds on top.

    Returns:
        Result dictionary with the command plan when dry_run is true, or the
        execution outcome when it is false.

    Note:
        Prefer write_score for ordinary composition: it runs the range and voice
        leading checks first and can take the MusicXML path when a key or time
        signature is needed. Come here to inspect what would be sent, or when the
        caret path is specifically what is wanted.

        With dry_run true this is a read: nothing is dispatched and Dorico need not
        even be running.
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


@mcp.tool(annotations=DESTROYS)
def export_musicxml(
    score: ScoreArg,
    path: Annotated[
        str | None,
        Field(
            description=(
                "Where to write the .musicxml file. An existing file at this path "
                "is overwritten. Omit to write to a temporary file and take the "
                "path from the answer."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Write a ScoreSpec to a MusicXML file on disk, without Dorico.

    Entirely offline: no connection is opened and Dorico need not be running. Key,
    time signature, clefs, dynamics and tempo all survive the round trip, which is
    what makes this the way to set the two signatures no popover will accept.

    Returns:
        Result dictionary with the outcome and the path that was written.

    Note:
        A specified path is overwritten without confirmation. Omit the path to
        write to a temporary file.

        To get the file into Dorico afterwards, import_musicxml opens it as a new
        flow, and write_score(method='musicxml') does both steps in one call. For a
        printable file rather than an interchange one, use export_pdf.
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


@mcp.tool(annotations=READS)
def read_project_info(
    path: Annotated[
        str,
        Field(
            description=(
                "Filesystem path to a saved .dorico project. Read straight from "
                "the file, so it can be any project on disk and not only the one "
                "open in Dorico."
            )
        ),
    ],
) -> dict[str, Any]:
    """Read metadata, flows, and player rosters from a saved .dorico file.

    Opens the project as the ZIP archive it is and reads the document and per-flow
    metadata out of it. Entirely offline: Dorico need not be running, and this works
    on a project nobody has open.

    Returns:
        Result dictionary with the document metadata, the flows, and the players.

    Note:
        This reads the project wrapper, not the music: for notes and rhythms, export
        with export_musicxml and read that with read_score. For the project open in
        Dorico right now, get_status reports its state and read_selection its
        selection.
    """
    return _read_project_info(path)


@mcp.tool(annotations=READS)
def read_score(
    path: Annotated[
        str,
        Field(description="Filesystem path to the MusicXML file to read."),
    ],
    bars: Annotated[
        str | None,
        Field(
            description=(
                "Which bars to read, as a filter string: '8' for one, '8-12' for a "
                "range, '8,10,12' for a list. Bars are counted from 1. Omit to "
                "read the whole score, which on a long one is a lot of output."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Read an existing MusicXML file measure by measure using music21.

    Entirely offline and read-only. This is the way to see pitches, which Dorico
    itself will not report over the Remote API.

    Returns:
        Structured dictionary with the metadata, parts, measures and note events.

    Note:
        Use the bars filter rather than reading everything when the question is
        local: a full read of a long score returns every note of every part.

        To see the music currently in Dorico, export it with export_musicxml first
        and read that. read_project_info reads a .dorico project wrapper instead,
        and read_selection reports the live selection without pitches.
    """
    try:
        content = musicxml.read_score(path, bars)
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}
    except ValueError as e:
        return {"success": False, "error": f"invalid bars selector {bars!r}: {e}"}
    return {"success": True, **content}


#: File suffixes Dorico writes from its MusicXML export filter.
_MUSICXML_SUFFIXES = (".musicxml", ".mxl", ".xml")


def _musicxml_snapshot(folder: Path) -> dict[Path, float]:
    """Map every MusicXML file in ``folder`` to its modification time."""
    return {
        f: f.stat().st_mtime
        for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in _MUSICXML_SUFFIXES
    }


def _newest_since(folder: Path, before: dict[Path, float]) -> Path | None:
    """Return the MusicXML file that appeared or changed since ``before``."""
    fresh = [
        f
        for f, mtime in _musicxml_snapshot(folder).items()
        if f not in before or mtime > before[f]
    ]
    return max(fresh, key=lambda f: f.stat().st_mtime) if fresh else None


@mcp.tool(annotations=ADDS)
async def read_open_score(
    export_dir: Annotated[
        str,
        Field(
            description=(
                "Folder Dorico's export dialog is pointed at. The newest "
                "MusicXML file that appears there is the one read back."
            )
        ),
    ],
    trigger: Annotated[
        bool,
        Field(
            description=(
                "True opens Dorico's MusicXML export dialog first. False skips "
                "that and reads whatever MusicXML already lies in the folder, "
                "which is what to use after a timeout or a manual export."
            )
        ),
    ] = True,
    wait_seconds: Annotated[
        float,
        Field(
            description=(
                "How long to wait for the file while a person confirms the "
                "dialog. The call returns as soon as one appears."
            )
        ),
    ] = 90.0,
) -> dict[str, Any]:
    """Read the whole open score back by way of a MusicXML export.

    The only route to the full contents of the score that is open. Reads over the
    Remote API see the selection alone, so everything else here works blind. This
    is what makes bar count, key, time signature and above all a pickup bar
    knowable instead of guessed.

    Returns:
        Result dictionary with the parsed summary, the pickup finding, the last
        bar number, and the file that was read.

    Note:
        Dorico's MusicXML export filter opens a modal dialog that requires user
        confirmation. This tool triggers the export dialog and awaits the resulting
        file in export_dir. Point the export dialog to export_dir once in Dorico to
        enable one-key export confirmations on subsequent reads.

        The result is a snapshot of the score at export time. Re-export after
        edits to refresh score state.
    """
    folder = Path(export_dir)
    if not folder.is_dir():
        return {"success": False, "error": f"not a folder: {folder}"}

    before = _musicxml_snapshot(folder)
    instructions = (
        f"Confirm Dorico's MusicXML export dialog with the target folder set to "
        f"{folder}. Untick 'export layouts as separate files' to get a single file."
    )

    if trigger:
        client = _client_instance()
        try:
            await client.connect()
            resp = await client.send("File.Export?FilterID=MusicXMLExportFilter")
        except DoricoConnectionError as e:
            return {"success": False, "error": str(e)}
        if resp.code != "kOK":
            return {"success": False, "error": f"Dorico refused the export ({resp.code})"}

        waited = 0.0
        while waited < wait_seconds:
            await asyncio.sleep(1.0)
            waited += 1.0
            if _newest_since(folder, before) is not None:
                break

    found = _newest_since(folder, before)
    if found is None and not trigger:
        # Nothing new, so fall back to the newest file already there.
        existing = _musicxml_snapshot(folder)
        found = max(existing, key=lambda f: existing[f]) if existing else None
    if found is None:
        return {
            "success": False,
            "error": "no MusicXML file appeared in the folder",
            "waiting_on": instructions,
            "retry": "call again with trigger=False once the export has been saved",
        }

    try:
        summary = musicxml.parse_musicxml(found)
    except Exception as e:  # noqa: BLE001 - music21 parsing fails in many ways
        return {"success": False, "error": f"could not read {found}: {e}"}

    pickup = summary.get("pickup") or {}
    bars = summary.get("measure_count") or 0
    return {
        "success": True,
        "file": str(found),
        "summary": summary,
        "pickup": pickup.get("present"),
        "last_bar_number": max(bars - 1, 0) if pickup.get("present") else bars,
        "navigation_note": (
            "Pass pickup=True to goto_bar and open_popover for this flow: Dorico "
            "leaves the pickup out of the bar numbering."
            if pickup.get("present")
            else "No pickup bar, so bar numbers and bar counts agree."
        ),
        "caveat": (
            "A snapshot taken at export time. Re-run read_open_score after score "
            "edits to ensure metadata reflects current score state."
        ),
    }


@mcp.tool(annotations=ADDS)
async def import_musicxml(
    path: Annotated[
        str,
        Field(
            description=(
                "Filesystem path to the MusicXML file to open. Dorico reads it "
                "from disk, so it has to be a path Dorico can reach."
            )
        ),
    ],
) -> dict[str, Any]:
    """Import a MusicXML file into Dorico via the Remote Control API.

    Dispatches File.Open with MusicXMLImportFilter.

    Returns:
        Result dictionary with success, supported, attempted,
        requires_confirmation, code, path and note.

    Note:
        An import arrives as a new flow rather than joining the one on screen, and
        Dorico may raise a player assignment prompt that waits for a person. While
        that prompt is open, later commands are accepted but sit unexecuted in the
        queue (docs/protocol.md, "Modal Dialogs").

        To add music into the flow already open, use write_score(method="caret").
        Use export_musicxml to produce the file in the first place, or
        write_score(method="musicxml") to do both steps in one call.
    """
    client = _client_instance()
    try:
        await client.connect()
        return await render.import_musicxml(client, path)
    except DoricoConnectionError as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------- offline music theory
@mcp.tool(annotations=READS)
def analyze_harmony(score: ScoreArg) -> dict[str, Any]:
    """Estimate the key of a ScoreSpec and name its chords in Roman numerals.

    Entirely offline: the score is analysed as data and Dorico is not involved, so
    this works on music that has never been in a project.

    Returns:
        Result dictionary with the key estimate, its confidence, and a Roman numeral
        reading of each sonority.

    Note:
        Key estimation relies on pitch distribution. For short or highly chromatic
        excerpts, verify the estimated tonal center before relying on Roman numerals.

        This answers what the harmony is. For whether the voices move well between
        those chords use check_voice_leading, for strict two-part exercises
        check_counterpoint, and for what could come next suggest_next_chord.
    """
    spec, err = _load_spec(score)
    if err is not None:
        return err
    assert spec is not None
    key_info = theory.detect_key(spec)
    key_name = spec.key or key_info.get("key")
    roman = theory.roman_numeral_analysis(spec, key_name) if key_name else []
    return {"success": True, "key": key_info, "roman": roman}


@mcp.tool(annotations=READS)
def check_voice_leading(score: ScoreArg) -> dict[str, Any]:
    """Audit a ScoreSpec for parallel fifths and octaves, overlaps and spacing.

    Entirely offline and read-only: nothing in the score is changed and Dorico is
    not involved. Written for part-writing of any number of voices, so a chorale or
    a string quartet is the natural input.

    Returns:
        Result dictionary with one entry per issue found, each naming the rule, the
        voices involved and where it happens. An empty list means nothing was found.

    Note:
        These are the common-practice rules, so a passage that breaks them on purpose
        is reported too. The findings are advice and nothing here rewrites the music.

        For a strict two-part species exercise, check_counterpoint applies the
        stricter set of rules that belongs to it. For what the chords are rather than
        how the voices move between them, use analyze_harmony.
    """
    spec, err = _load_spec(score)
    if err is not None:
        return err
    assert spec is not None
    issues = theory.check_voice_leading(spec, key=spec.key)
    return {"success": True, "issues": issues, "ok": not issues}


@mcp.tool(annotations=READS)
def suggest_next_chord(
    key: Annotated[
        str,
        Field(
            description=(
                "The tonal centre the numerals are read against, e.g. 'C major', "
                "'A minor' or 'F# major'. The numerals mean nothing without it."
            )
        ),
    ],
    progression: Annotated[
        list[str],
        Field(
            description=(
                "The chords so far as Roman numerals, in order, e.g. "
                "['I', 'vi', 'ii']. Case carries the quality: upper case is major, "
                "lower case minor. The last entry is the one being continued from."
            )
        ),
    ],
) -> dict[str, Any]:
    """Suggest functional continuations for a Roman numeral progression.

    Entirely offline and read-only: this reasons about numerals as symbols and never
    touches a score or Dorico.

    Returns:
        Result dictionary with the candidate next chords, each carrying its harmonic
        function and why it follows.

    Note:
        Suggestions come from common-practice function, so they describe what usually
        follows rather than what must. Nothing is written anywhere. To hear a
        candidate, put it in a ScoreSpec and use write_score.

        This takes numerals, not notes. To get numerals out of actual music, run
        analyze_harmony first and feed its reading in here.
    """
    try:
        suggestions = theory.suggest_next_chord(key, progression)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "suggestions": suggestions}


@mcp.tool(annotations=READS)
def instrument_range(
    instrument: Annotated[
        str,
        Field(
            description=(
                "Instrument to look up, e.g. 'violin', 'flute', 'cello' or "
                "'trumpet'. Matched against the standard orchestral names."
            )
        ),
    ],
    pitch: Annotated[
        str | None,
        Field(
            description=(
                "A pitch to test in scientific notation, e.g. 'C4' or 'A5'. Omit "
                "to get the full compass instead of a yes or no."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Look up a standard instrument compass, or test one pitch against it.

    Two questions through one door: without a pitch it reports the range, with one it
    reports whether that note is inside it. Entirely offline and read-only.

    Returns:
        Result dictionary with in_range when a pitch was given, or the lowest and
        highest playable pitches when it was not.

    Note:
        These are the standard written ranges for a competent player, not the limits
        of the instrument or of a particular one: professionals exceed them and
        beginners do not reach them.

        Worth calling before write_score when writing for an instrument, since
        write_score with preflight left on runs the same check over a whole score and
        reports what falls outside.
    """
    try:
        if pitch is not None:
            return {"success": True, "in_range": theory.note_in_range(instrument, pitch)}
        low, high = theory.instrument_bounds(instrument)
        return {"success": True, "lowest": low.nameWithOctave, "highest": high.nameWithOctave}
    except ValueError as e:
        return {"success": False, "error": str(e)}


@mcp.tool(annotations=READS)
def check_counterpoint(
    cantus_firmus: Annotated[
        list[str],
        Field(
            description=(
                "The given line, as scientific pitch names in order, e.g. "
                "['D4', 'F4', 'E4', 'D4']."
            )
        ),
    ],
    counterpoint: Annotated[
        list[str],
        Field(
            description=(
                "The line written against it, same notation and the same length: "
                "first species is note against note, so the two lists pair up one "
                "to one."
            )
        ),
    ],
    species: Annotated[
        int,
        Field(
            description=(
                "Which species to check. Only 1, note against note, is "
                "implemented. Any other value is refused rather than approximated."
            )
        ),
    ] = 1,
) -> dict[str, Any]:
    """Check a two-part first-species counterpoint against the classic rules.

    Entirely offline and read-only. Enforces the strict set: begin and end on a
    perfect consonance, consonant verticals only, no consecutive perfect fifths,
    octaves or unisons, no voice crossing, and one melodic climax in the
    counterpoint.

    Returns:
        Result dictionary with one entry per issue, each naming the rule and the beat
        it happens on. An empty list means the exercise passes.

    Note:
        Only first species is implemented, so passing any other species is refused
        rather than checked loosely. The two lines must be the same length, since
        note against note pairs them one to one.

        These rules are stricter than ordinary part-writing on purpose. For a
        chorale or a quartet use check_voice_leading, which applies the
        common-practice rules to any number of voices.
    """
    try:
        issues = theory.check_species_counterpoint(
            cantus_firmus, counterpoint, species=species
        )
    except NotImplementedError as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "issues": issues, "ok": not issues}


# ------------------------------------------------------------- catalog discovery
@mcp.tool(annotations=READS)
def search_commands(
    query: Annotated[
        str,
        Field(
            description=(
                "Substring matched case-insensitively against command IDs and "
                "their documentation. Empty matches everything, which with a limit "
                "is how to browse a category."
            )
        ),
    ] = "",
    category: Annotated[
        str | None,
        Field(
            description=(
                "Command family to restrict to, e.g. 'NoteInput', 'Edit' or "
                "'Play', matched case-insensitively. Omit for every family."
            )
        ),
    ] = None,
    status: Annotated[
        str | None,
        Field(
            description=(
                "How well established the command is: 'verified', 'reachable', "
                "'unavailable', 'broken' or 'untested'. Filter on 'verified' for "
                "the ones actually seen to work. Omit for every status."
            )
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            description=(
                "Most entries to return. The answer still reports the total number "
                "of matches, so a truncated result says so."
            )
        ),
    ] = 40,
) -> dict[str, Any]:
    """Search the Dorico command catalog by query, category, or status.

    Entirely offline: the catalog ships with this server, so nothing is asked of
    Dorico and it need not be running.

    Returns:
        Result dictionary with the total number of matches, a count per status, and
        the matching commands with their IDs, parameters and documentation.

    Note:
        This is how to find a command ID and what it takes before sending it with
        run_command, and how to see the status that tells you how far to trust it.
        Only 'verified' rows have been observed to work.

        Prefer a dedicated tool where one exists: much of what the catalog can reach
        is already covered by write_score, transpose, playback, save and the rest,
        with validation run_command does not perform.
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
