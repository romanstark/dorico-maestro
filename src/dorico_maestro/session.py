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
    supported; anything else (bad letter, double accidental, missing/garbled
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


class NoteInputSession:
    """Async context manager wrapping Dorico's note-input caret lifecycle.

    ``__aenter__`` sends ``NoteInput.Exit`` followed by ``NoteInput.Enter``.
    Because ``NoteInput.Enter`` toggles note input, sending ``NoteInput.Exit``
    first guarantees a known starting state without toggling off an active caret.
    What it does not guarantee is a position: the caret comes back at the start of
    the current selection, or somewhere unrelated when nothing is selected, never
    where it stood before. Move to the bar you want inside the block
    (Dorico Elements 6.2.30).
    ``__aexit__`` ensures ``NoteInput.Exit`` is sent even if an exception occurs.
    Inside the ``async with`` block, set a duration then add pitches/rests.
    """

    def __init__(self, client: CommandSender) -> None:
        self._client = client

    async def __aenter__(self) -> Self:
        await self._client.send("NoteInput.Exit")
        await self._client.send("NoteInput.Enter")
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.send("NoteInput.Exit")

    async def set_duration(self, d: NoteDuration) -> None:
        """Set the caret's note value for subsequent pitches/rests."""
        await self._client.send(f"NoteInput.NoteValue?LogDuration={DURATION_TO_DORICO[d]}")

    async def pitch(self, spec: str) -> None:
        """Input a single pitch (e.g. ``"F#5"``) at the caret."""
        for cmd in pitch_commands(spec):
            await self._client.send(cmd)

    async def rest(self) -> None:
        """Input a rest of the current duration at the caret."""
        await self._client.send("NoteInput.RestMode")
