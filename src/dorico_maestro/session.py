"""Note-input helpers: the caret lifecycle and pitch/rest entry.

All note entry in Dorico happens inside a *note-input* session (caret visible).
:class:`NoteInputSession` is an async context manager that opens the session on
entry and closes it on exit — guaranteed, even if the body raises. The pure
functions :func:`parse_pitch` / :func:`pitch_commands` translate a scientific
pitch name (``"F#5"``) into the command string(s) Dorico expects.

This module only produces/sends command strings; it holds no music21 or
transport logic of its own beyond calling ``client.send``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Self

from dorico_maestro.models import DURATION_TO_DORICO, NoteDuration

if TYPE_CHECKING:
    from dorico_maestro.client import DoricoClient


# letter (A-G) · optional single accidental (# or b) · signed octave
_PITCH_RE = re.compile(r"^([A-Ga-g])([#b])?(-?\d+)$")

# Accidental character -> Dorico's enum value. Dorico 6's keycommands.json bakes
# the k-prefixed form (NoteInput.SetAccidental?Type=kSharp), so we match it.
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
    """Command string(s) to input a single pitch, incl. an accidental pre-step.

    e.g. ``"F#5" -> ["NoteInput.SetAccidental?Type=kSharp",
    "NoteInput.Pitch?Pitch=F&OctaveValue=5"]``.
    """
    letter, octave, accidental = parse_pitch(spec)
    cmds: list[str] = []
    if accidental:
        cmds.append(f"NoteInput.SetAccidental?Type={accidental}")
    cmds.append(f"NoteInput.Pitch?Pitch={letter}&OctaveValue={octave}")
    return cmds


class NoteInputSession:
    """Async context manager wrapping Dorico's note-input caret lifecycle.

    ``__aenter__`` sends ``NoteInput.Enter``; ``__aexit__`` sends
    ``NoteInput.Exit`` (always, even on error, so the caret is never left open).
    Inside the ``async with`` block, set a duration then add pitches/rests.
    """

    def __init__(self, client: DoricoClient) -> None:
        self._client = client

    async def __aenter__(self) -> Self:
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
