"""Turn a typed score model into live Dorico input.

The input model is :class:`~dorico_maestro.music.score.ScoreSpec`. This module
has two clearly separated halves:

* A pure planner (:func:`bar_count`, :func:`plan_staff`, :func:`plan_flow`)
  that turns the typed score model into an ordered list of Dorico command
  strings plus honest warnings. It touches no transport and is fully unit
  testable without a fake client.
* Thin async wrappers (:func:`render_score`, :func:`import_musicxml`) that
  send those commands through a client-like object (anything exposing
  ``async send`` / ``async status``), reusing
  :class:`~dorico_maestro.session.NoteInputSession` so the caret is always
  closed, even when a send raises mid-render.

The caret path uses only commands the Remote API accepts and positions the caret
deterministically per staff (``MoveUpTop`` / ``MoveLeftBar`` / ``MoveDown``)
rather than relying on an undo-to-empty state.
Popover-only elements (key, time, clef, named dynamics, tempo) cannot be entered
through the Remote API, so they are dropped with a warning here and belong on
the MusicXML path instead. Response code kOK from Dorico confirms acceptance,
never verified effect: live reports include :data:`_KOK_CAVEAT`.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dorico_maestro.models import (
    ARTICULATION_TO_DORICO,
    DURATION_TO_DORICO,
    Articulation,
    NoteDuration,
    TimeSignature,
)
from dorico_maestro.music.score import Part, ScoreSpec, Staff
from dorico_maestro.session import NoteInputSession, pitch_commands

if TYPE_CHECKING:
    from dorico_maestro.client import DoricoClient

# Response code kOK from Dorico indicates command acceptance by the queue,
# not guaranteed score mutation. Verify via playback or visual inspection.
_KOK_CAVEAT = (
    "kOK from Dorico means the command was accepted, not that the note actually "
    "landed. Verify by playback or by looking at the score."
)

# Fallback bar length (4/4) when a spec carries no time signature. The live caret
# cannot set the time signature, so this only drives the MoveLeftBar rewind count.
_DEFAULT_BAR_QUARTER_LENGTH = 4.0

# Dorico's pushed-status articulation flag -> the SetArticulation value that toggles
# it. Used to defensively clear a caret that a prior edit left dirty (see
# _clear_caret_articulations): SetArticulation is a persistent toggle, so a leftover
# articulation would otherwise land on every rendered note.
_STATUS_ARTICULATION = {
    "articulationAccent": "kAccent",
    "articulationStaccato": "kStaccato",
    "articulationMarcato": "kMarcato",
    "articulationTenuto": "kTenuto",
    "articulationStaccatissimo": "kStaccatissimo",
    "articulationStaccatoTenuto": "kStaccatoTenuto",
    "articulationStressed": "kStress",
    "articulationUnstressed": "kUnstress",
}


@dataclass(slots=True)
class RenderReport:
    """Outcome of a live render (or a dry-run plan).

    The experimental flag is True when the plan relies on unverified caret
    behavior (multiple parts or multi-voice staves).
    """

    ok: bool
    parts_rendered: int
    commands_planned: int
    commands_sent: int
    warnings: list[str]
    experimental: bool


# --------------------------------------------------------------------------- #
# Pure planner
# --------------------------------------------------------------------------- #


def bar_count(part: Part, bar_quarter_length: float) -> int:
    """Return how many bars ``part`` spans at the given bar length.

    Computed as ``ceil(longest voice / bar length)`` across every staff/voice of
    the part, so a ragged part still rewinds far enough. ``bar_quarter_length``
    is in quarters per bar and must be positive. Anything else raises
    :class:`ValueError`.
    """
    if bar_quarter_length <= 0:
        raise ValueError(f"bar_quarter_length must be > 0, got {bar_quarter_length}")
    longest = 0.0
    for staff in part.staves:
        for voice in staff.voices:
            longest = max(longest, voice.quarter_length)
    return ceil(longest / bar_quarter_length)


def plan_staff(staff: Staff, *, reemit_duration: bool = True) -> tuple[list[str], list[str]]:
    """Plan the caret commands for one staff's voice 1, left to right.

    Returns ``(commands, warnings)``. The base note value is emitted at the staff
    start (when ``reemit_duration``) and again whenever it changes. Dots repeat
    ``CycleNumDots``. Chords are wrapped in a ``StartEndChord`` pair. Ties
    (``Tie``) and rests (``RestMode``) are emitted.

    Articulations need care. ``NoteInput.SetArticulation`` is a persistent caret
    toggle, not a one-shot per note: an articulation stays on and lands on every
    following note until toggled off again. So we track the active set and
    reconcile it before each note, turning off the ones no longer wanted and
    turning on the newly wanted ones. Whatever is still on at the end of the
    staff is then cleared, so articulations never leak onto later notes.

    Named dynamics are never emitted. They are dropped with a warning (use the
    MusicXML path or open_popover). Any 2nd+ voice is skipped with an experimental
    warning (the MVP renders voice 1 only).
    """
    commands: list[str] = []
    warnings: list[str] = []

    if staff.clef is not None:
        warnings.append(
            f"staff {staff.index}: clef {staff.clef.value!r} is not enterable live; "
            "set it in Dorico or use export_musicxml"
        )

    voices = staff.voices
    if not voices:
        return commands, warnings

    primary = next((v for v in voices if v.index == 1), voices[0])
    for voice in voices:
        if voice is primary:
            continue
        warnings.append(
            f"staff {staff.index} voice {voice.index}: additional voice skipped "
            "(experimental: the MVP renders voice 1 only)"
        )

    def _toggle(arts: list[Articulation]) -> None:
        for art in sorted(arts, key=lambda a: a.value):
            commands.append(f"NoteInput.SetArticulation?Value={ARTICULATION_TO_DORICO[art]}")

    active: set[Articulation] = set()
    current: NoteDuration | None = None
    for ei, ev in enumerate(primary.events):
        if ev.duration != current and (ei != 0 or reemit_duration):
            commands.append(f"NoteInput.NoteValue?LogDuration={DURATION_TO_DORICO[ev.duration]}")
        current = ev.duration

        for _ in range(ev.dots):
            commands.append("NoteInput.CycleNumDots")

        # Reconcile the persistent articulation state BEFORE entering the note.
        want: set[Articulation] = set() if ev.is_rest else set(ev.articulations)
        _toggle(list(active - want))  # turn off no-longer-wanted
        _toggle(list(want - active))  # turn on newly wanted
        active = want

        if ev.is_rest:
            commands.append("NoteInput.RestMode")
        elif ev.is_chord:
            commands.append("NoteInput.StartEndChord")
            for name in ev.pitches:
                commands.extend(pitch_commands(name))
            commands.append("NoteInput.StartEndChord")
        else:
            commands.extend(pitch_commands(ev.pitches[0]))

        if ev.tie and not ev.is_rest:
            commands.append("NoteInput.Tie")

        if ev.slur == "start":
            commands.append("NoteInput.SlurStart")
        elif ev.slur == "stop":
            commands.append("NoteInput.SlurStop")

        if ev.dynamic is not None:
            warnings.append(
                f"staff {staff.index} voice {primary.index} event {ei}: "
                f"dynamic {ev.dynamic.value!r} dropped: not enterable live; use export_musicxml"
            )

    # Clear any articulation still toggled on so it can't leak past this staff.
    _toggle(list(active))

    return commands, warnings


def plan_flow(spec: ScoreSpec) -> tuple[list[str], list[str]]:
    """Plan the full ordered command list for the whole flow in one session.

    Returns ``(commands, warnings)``. The list is wrapped in
    ``NoteInput.Enter`` … ``NoteInput.Exit`` and walks every staff in system
    order. Before each staff, including the first, the caret is repositioned
    deterministically with ``MoveUpTop`` -> ``MoveLeftBar`` × :func:`bar_count` ->
    ``MoveDown`` × ``g`` (the global staff index). Rewinding for the first staff
    too means the caret lands on bar 1 of the top staff on its own, so a bare
    ``NoteInput.Enter`` on a fresh flow is enough. :func:`render_score` executes
    exactly this list.
    """
    commands: list[str] = ["NoteInput.Enter"]
    warnings: list[str] = []
    bar_ql = _bar_quarter_length(spec)

    g = 0
    for part in spec.parts:
        bars = bar_count(part, bar_ql)
        for staff in part.staves:
            commands.append("NoteInput.MoveUpTop")
            commands.extend(["NoteInput.MoveLeftBar"] * bars)
            commands.extend(["NoteInput.MoveDown"] * g)
            staff_cmds, staff_warnings = plan_staff(staff, reemit_duration=True)
            commands.extend(staff_cmds)
            warnings.extend(f"{part.name}/{w}" for w in staff_warnings)
            g += 1

    commands.append("NoteInput.Exit")
    return commands, warnings


def _bar_quarter_length(spec: ScoreSpec) -> float:
    """Return the quarter-notes per bar for ``spec.time``, falling back to 4/4."""
    if spec.time:
        try:
            return TimeSignature.parse(spec.time).bar_quarter_length
        except ValueError:
            pass
    return _DEFAULT_BAR_QUARTER_LENGTH


def _is_experimental(spec: ScoreSpec) -> bool:
    """Return True when the plan relies on unverified caret behaviour.

    :class:`RenderReport` documents what counts as unverified.
    """
    if len(spec.parts) > 1:
        return True
    return any(len(staff.voices) > 1 for part in spec.parts for staff in part.staves)


# --------------------------------------------------------------------------- #
# Async wrappers (the only Dorico-facing code)
# --------------------------------------------------------------------------- #


async def _clear_caret_articulations(client: DoricoClient) -> None:
    """Toggle off any articulation the caret still has active before a render.

    SetArticulation is a persistent toggle, and an interrupted edit can leave
    one active. Dorico exposes this in pushed status via articulation* flags.
    plan_staff assumes a clean initial state, so we read status and toggle off
    only active flags.
    """
    try:
        status = await client.status()
    except Exception:  # noqa: BLE001 - a fresh caret is already clean; skip on any read error
        return
    for flag, value in _STATUS_ARTICULATION.items():
        if status.get(flag):
            await client.send(f"NoteInput.SetArticulation?Value={value}")


async def render_score(
    client: DoricoClient, spec: ScoreSpec, *, dry_run: bool = False
) -> RenderReport:
    """Render spec into Dorico via the live caret path.

    Plans commands with plan_flow and transmits them inside a NoteInputSession,
    ensuring clean caret entry and exit even on exceptions.
    """
    commands, warnings = plan_flow(spec)
    report_warnings = [*warnings, _KOK_CAVEAT]
    experimental = _is_experimental(spec)

    if dry_run:
        return RenderReport(
            ok=True,
            parts_rendered=len(spec.parts),
            commands_planned=len(commands),
            commands_sent=0,
            warnings=report_warnings,
            experimental=experimental,
        )

    # plan_flow wraps its output in Enter and Exit; NoteInputSession manages
    # those, so only interior commands are transmitted.
    interior = commands[1:-1]
    async with NoteInputSession(client):
        await _clear_caret_articulations(client)
        for command in interior:
            await client.send(command)

    return RenderReport(
        ok=True,
        parts_rendered=len(spec.parts),
        commands_planned=len(commands),
        commands_sent=len(commands),
        warnings=report_warnings,
        experimental=experimental,
    )


async def import_musicxml(
    client: DoricoClient, path: str | Path, *, filter_id: str = "MusicXMLImportFilter"
) -> dict[str, Any]:
    """Import a MusicXML file into Dorico via the Remote Control API.

    Transmits the file path with forward slashes without URL encoding.
    Dorico imports the file into a new flow and may display a player confirmation
    dialog. To add music into an existing sheet instead, use write_score(method="caret").

    Paths containing '&', '?', or '#' are rejected before dispatch.

    Returns:
        Dict with success, supported, attempted, requires_confirmation, code, path,
        and note.
    """
    abs_path = str(Path(path).resolve())
    wire_path = abs_path.replace("\\", "/")
    blocked = [c for c in ("&", "?", "#") if c in wire_path]
    if blocked:
        return {
            "success": False,
            "supported": True,
            "attempted": False,
            "requires_confirmation": False,
            "code": None,
            "path": abs_path,
            "note": (
                f"Path contains {blocked}: characters Dorico cannot receive un-decoded. "
                "Move or rename the file to a path without & ? #, "
                "or use write_score(method='caret')."
            ),
        }

    resp = await client.send(f"File.Open?File={wire_path}&FilterID={filter_id}")
    accepted = resp.code == "kOK"
    note = (
        "Import launched as Dorico's normal MusicXML import: it creates a NEW flow "
        "and may pop a 'create a new player?' dialog you must confirm in Dorico. To "
        "merge into an existing sheet instead, use write_score(method='caret')."
    )
    if not accepted:
        note = f"Dorico did not accept the open (code={resp.code}). " + note

    return {
        "success": accepted,
        "supported": True,
        "attempted": True,
        "requires_confirmation": True,
        "code": resp.code,
        "path": abs_path,
        "note": note,
    }
