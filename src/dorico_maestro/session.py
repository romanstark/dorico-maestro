"""Note input helpers: caret lifecycle and pitch/rest commands.

All note entry in Dorico occurs inside a note-input session where the caret
is active. NoteInputSession provides an asynchronous context manager ensuring
clean entry (NoteInput.Enter) and exit (NoteInput.Exit). The helper functions
parse_pitch and pitch_commands translate scientific pitch notation (such as
"F#5") into Dorico command strings.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Self

from dorico_maestro.models import DURATION_TO_DORICO, NoteDuration

if TYPE_CHECKING:
    from dorico_maestro.client import CommandSender


# letter (A-G) · optional single accidental (# or b) · signed octave
_PITCH_RE = re.compile(r"^([A-Ga-g])([#b])?(-?\d+)$")

# Accidental character -> Dorico enum value matching NoteInput.SetAccidental?Type=kSharp.
_ACCIDENTAL = {"#": "kSharp", "b": "kFlat"}


def parse_pitch(spec: str) -> tuple[str, int, str | None]:
    """Parse a scientific pitch name into ``(letter, octave, accidental)``.

    ``"C4" -> ("C", 4, None)``, ``"F#5" -> ("F", 5, "kSharp")``,
    ``"Bb3" -> ("B", 3, "kFlat")``. ``accidental`` is Dorico's enum value
    (``"kSharp"``/``"kFlat"``) or ``None``. Only a single accidental is
    supported. Anything else (bad letter, double accidental, missing/garbled
    octave) raises :class:`ValueError` rather than being silently misparsed.
    """
    m = _PITCH_RE.match(spec.strip())
    if m is None:
        raise ValueError(f"invalid pitch: {spec!r} (expected e.g. 'C4', 'F#5', 'Bb3')")
    letter, acc, octave = m.group(1).upper(), m.group(2), int(m.group(3))
    return letter, octave, (_ACCIDENTAL[acc] if acc else None)


def pitch_commands(spec: str) -> list[str]:
    """Generate command string(s) to input a single pitch with an accidental.

    e.g. ``"F#5" -> ["NoteInput.SetAccidental?Type=kSharp",
    "NoteInput.Pitch?Pitch=F&OctaveValue=5"]``, and
    ``"D4" -> ["NoteInput.SetAccidental?Type=kNatural",
    "NoteInput.Pitch?Pitch=D&OctaveValue=4"]``.

    Because Dorico's ``NoteInput.Pitch`` interprets pitch letters diatonically
    according to the active key signature, each note is explicitly preceded by
    ``NoteInput.SetAccidental`` (using ``kNatural`` for unaltered pitches) to
    guarantee absolute pitch spelling. Dorico still suppresses the sign where the
    key makes it redundant (Dorico Elements 6.2.30).
    """
    letter, octave, accidental = parse_pitch(spec)
    return [
        f"NoteInput.SetAccidental?Type={accidental or 'kNatural'}",
        f"NoteInput.Pitch?Pitch={letter}&OctaveValue={octave}",
    ]


# The Y key: a rest while rest mode is on, else the previous pitch again.
REST_KEY = "NoteInput.Pitch?Pitch=X"


def rest_commands(rest_mode_on: bool) -> list[str]:
    """Return the commands for one rest, leaving rest mode as it was found."""
    if rest_mode_on:
        return [REST_KEY]
    return ["NoteInput.RestMode", REST_KEY, "NoteInput.RestMode"]


async def current_status(client: object) -> dict[str, object]:
    """Return Dorico's current status, asked fresh where the client can."""
    for name in ("fresh_status", "status"):
        method = getattr(client, name, None)
        if method is not None:
            return dict(await method())
    return {}


class NoteInputSession:
    """Async context manager wrapping Dorico's note-input caret lifecycle.

    When note input is already running (after ``goto_bar``), the session sends
    nothing on entry or exit, so the caret stays where it was put and the next
    call continues from there. Re-entering would lose it: ``NoteInput.Exit`` then
    ``NoteInput.Enter`` brings the caret back at the start of the selection, or
    at the viewport when nothing is selected (Dorico Elements 6.2.30).

    When note input is off, ``__aenter__`` sends ``NoteInput.Exit`` then
    ``NoteInput.Enter`` (Enter toggles, so Exit first gives a known state) and
    ``__aexit__`` sends ``NoteInput.Exit`` even if an exception occurs.
    ``keep_caret=False`` always re-enters and exits, for callers that position
    the caret themselves.
    """

    def __init__(self, client: CommandSender, *, keep_caret: bool = True) -> None:
        self._client = client
        self._keep_caret = keep_caret
        self._was_active = False

    async def __aenter__(self) -> Self:
        self._was_active = False
        if self._keep_caret:
            self._was_active = bool((await current_status(self._client)).get("noteInputActive"))
        if not self._was_active:
            await self._client.send("NoteInput.Exit")
            await self._client.send("NoteInput.Enter")
        return self

    async def __aexit__(self, *exc: object) -> None:
        if not self._was_active:
            await self._client.send("NoteInput.Exit")

    async def set_duration(self, d: NoteDuration) -> None:
        """Set the caret's note value for subsequent pitches/rests."""
        await self._client.send(f"NoteInput.NoteValue?LogDuration={DURATION_TO_DORICO[d]}")

    async def pitch(self, spec: str) -> None:
        """Input a single pitch (e.g. ``"F#5"``) at the caret."""
        for cmd in pitch_commands(spec):
            await self._client.send(cmd)

    async def rest(self) -> None:
        """Input a rest of the current duration at the caret, leaving rest mode as found."""
        rest_mode = bool((await current_status(self._client)).get("restMode"))
        for command in rest_commands(rest_mode):
            await self._client.send(command)
