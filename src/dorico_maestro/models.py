"""Small value types and enums shared across the package."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AWAITING_APPROVAL = "awaiting_approval"
    CONNECTED = "connected"
    ERROR = "error"


class CmdStatus(str, Enum):
    """Our integration status for a Dorico command.

    ``kOK`` from Dorico only means *accepted*, so this tracks what we have
    actually confirmed against a live instance (see ``docs/protocol.md``).
    """

    VERIFIED = "verified"   # returned kOK against real Dorico
    BROKEN = "broken"       # returned kUnknownCommand / kError (wrong name or shape)
    UNTESTED = "untested"   # in the catalog but not yet probed live


class NoteDuration(str, Enum):
    WHOLE = "whole"
    HALF = "half"
    QUARTER = "quarter"
    EIGHTH = "eighth"
    SIXTEENTH = "sixteenth"
    THIRTY_SECOND = "32nd"
    SIXTY_FOURTH = "64th"


# Dorico's ``NoteInput.NoteValue?LogDuration=`` value (British note names) per
# duration length.
DURATION_TO_DORICO: dict[NoteDuration, str] = {
    NoteDuration.WHOLE: "kSemibreve",
    NoteDuration.HALF: "kMinim",
    NoteDuration.QUARTER: "kCrotchet",
    NoteDuration.EIGHTH: "kQuaver",
    NoteDuration.SIXTEENTH: "kSemiQuaver",
    NoteDuration.THIRTY_SECOND: "kDemiSemiQuaver",
    NoteDuration.SIXTY_FOURTH: "kHemiDemiSemiQuaver",
}


class Dynamic(str, Enum):
    """Named dynamic markings.

    There is deliberately **no** ``DYNAMIC_TO_DORICO`` map: named dynamics are a
    Dorico popover the Remote API cannot fill (same class as key/time signatures),
    so they travel the MusicXML path only and are dropped-with-warning on the live
    caret path.
    """

    PPP = "ppp"
    PP = "pp"
    P = "p"
    MP = "mp"
    MF = "mf"
    F = "f"
    FF = "ff"
    FFF = "fff"
    SF = "sf"
    SFZ = "sfz"
    FP = "fp"
    RF = "rf"


class Articulation(str, Enum):
    """Note articulations enterable live via ``NoteInput.SetArticulation``."""

    ACCENT = "accent"
    MARCATO = "marcato"
    STACCATO = "staccato"
    STACCATISSIMO = "staccatissimo"
    STACCATO_TENUTO = "staccato-tenuto"
    TENUTO = "tenuto"
    STRESS = "stress"
    UNSTRESS = "unstress"


class Clef(str, Enum):
    """Staff clefs. MusicXML-only: the live caret path ignores clefs."""

    TREBLE = "treble"
    BASS = "bass"
    ALTO = "alto"
    TENOR = "tenor"
    TREBLE_8VB = "treble8vb"
    PERCUSSION = "percussion"


# Articulation -> Dorico ``SetArticulation?Value=`` enum (all VERIFIED in
# commands.yaml).
ARTICULATION_TO_DORICO: dict[Articulation, str] = {
    Articulation.ACCENT: "kAccent",
    Articulation.MARCATO: "kMarcato",
    Articulation.STACCATO: "kStaccato",
    Articulation.STACCATISSIMO: "kStaccatissimo",
    Articulation.STACCATO_TENUTO: "kStaccatoTenuto",
    Articulation.TENUTO: "kTenuto",
    Articulation.STRESS: "kStress",
    Articulation.UNSTRESS: "kUnstress",
}

# Articulation -> music21 articulation class name (MusicXML path). Kept as plain
# strings so ``models`` stays pure stdlib (no music21 import).
ARTICULATION_TO_MUSIC21: dict[Articulation, str] = {
    Articulation.ACCENT: "Accent",
    Articulation.MARCATO: "StrongAccent",
    Articulation.STACCATO: "Staccato",
    Articulation.STACCATISSIMO: "Staccatissimo",
    Articulation.STACCATO_TENUTO: "DetachedLegato",
    Articulation.TENUTO: "Tenuto",
    Articulation.STRESS: "Stress",
    Articulation.UNSTRESS: "Unstress",
}

# Clef -> music21 clef class name (MusicXML path). Plain strings (see above).
CLEF_TO_MUSIC21: dict[Clef, str] = {
    Clef.TREBLE: "TrebleClef",
    Clef.BASS: "BassClef",
    Clef.ALTO: "AltoClef",
    Clef.TENOR: "TenorClef",
    Clef.TREBLE_8VB: "Treble8vbClef",
    Clef.PERCUSSION: "PercussionClef",
}


# Note value -> its length in quarter notes (undotted). Used by score bar math.
DURATION_QUARTER_LENGTH: dict[NoteDuration, float] = {
    NoteDuration.WHOLE: 4.0,
    NoteDuration.HALF: 2.0,
    NoteDuration.QUARTER: 1.0,
    NoteDuration.EIGHTH: 0.5,
    NoteDuration.SIXTEENTH: 0.25,
    NoteDuration.THIRTY_SECOND: 0.125,
    NoteDuration.SIXTY_FOURTH: 0.0625,
}

# Note value -> music21 ``duration.type`` string. Note music21 spells sixteenth
# as "16th" (not "sixteenth") while our SIXTEENTH *value* is "sixteenth".
DURATION_TO_MUSIC21: dict[NoteDuration, str] = {
    NoteDuration.WHOLE: "whole",
    NoteDuration.HALF: "half",
    NoteDuration.QUARTER: "quarter",
    NoteDuration.EIGHTH: "eighth",
    NoteDuration.SIXTEENTH: "16th",
    NoteDuration.THIRTY_SECOND: "32nd",
    NoteDuration.SIXTY_FOURTH: "64th",
}

# Inverse of DURATION_TO_MUSIC21, for reading music21 back into a NoteDuration.
DURATION_FROM_MUSIC21: dict[str, NoteDuration] = {
    v: k for k, v in DURATION_TO_MUSIC21.items()
}


def dotted_multiplier(dots: int) -> float:
    """Return the duration multiplier for ``dots`` rhythmic dots.

    ``0 -> 1.0``, ``1 -> 1.5``, ``2 -> 1.75`` (each dot adds half of the previous
    increment: ``1 + 1/2 + 1/4 + …``). Pure; used by the score bar math. A
    negative ``dots`` count is nonsensical and raises :class:`ValueError`.
    """
    if dots < 0:
        raise ValueError(f"dots must be >= 0, got {dots}")
    total = 0.0
    increment = 1.0
    for _ in range(dots + 1):
        total += increment
        increment /= 2.0
    return total


@dataclass(slots=True, frozen=True)
class TimeSignature:
    """A time signature such as ``6/8``.

    Parsed from / rendered to the ``"numerator/denominator"`` string used across
    the wire format (matching ``generate_musicxml(time_signature="4/4")``).
    """

    numerator: int
    denominator: int

    @classmethod
    def parse(cls, text: str) -> TimeSignature:
        """Parse ``"6/8"`` into ``TimeSignature(6, 8)``.

        Both parts must be positive integers; anything else (missing slash,
        non-integer, non-positive) raises :class:`ValueError`.
        """
        parts = text.strip().split("/")
        if len(parts) != 2:
            raise ValueError(f"invalid time signature: {text!r} (expected e.g. '4/4')")
        try:
            numerator, denominator = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError(
                f"invalid time signature: {text!r} (expected e.g. '4/4')"
            ) from exc
        if numerator <= 0 or denominator <= 0:
            raise ValueError(
                f"invalid time signature: {text!r} (numerator/denominator must be > 0)"
            )
        return cls(numerator, denominator)

    @property
    def ratio_string(self) -> str:
        """The ``"numerator/denominator"`` string, e.g. ``"6/8"``."""
        return f"{self.numerator}/{self.denominator}"

    @property
    def bar_quarter_length(self) -> float:
        """Length of one bar in quarter notes: ``numerator * 4 / denominator``."""
        return self.numerator * 4.0 / self.denominator


@dataclass(slots=True, frozen=True)
class InstrumentSpec:
    """A named instrument with an optional playable range.

    ``lowest``/``highest`` are scientific pitch names (``"A2"``) that, when
    present, override ``theory._RANGES`` for range checks.
    """

    name: str
    abbreviation: str | None = None
    midi_program: int | None = None
    lowest: str | None = None
    highest: str | None = None


@dataclass(slots=True)
class Response:
    """A reply to a Dorico command.

    Note: ``ok`` reflects Dorico's response *code* (kOK vs kError/kUnknownCommand).
    kOK means "command accepted", NOT that the intended musical effect happened —
    verify via status / MusicXML / the composer's eyes.
    """

    ok: bool
    code: str | None = None
    detail: str | None = None
    data: dict | None = None

    @property
    def failed(self) -> bool:
        return not self.ok
