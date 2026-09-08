"""Typed input schemas for MCP tools accepting structured score definitions.

These models expose explicit JSON schema definitions for FastMCP tools that
accept score specifications (`ScoreSpec`), including `write_score`,
`render_to_dorico`, `export_musicxml`, `analyze_harmony`, and
`check_voice_leading`.

Validation of musical constraints, pitch parsing, and score normalization
remains encapsulated in `dorico_maestro.music.score`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from dorico_maestro.models import Articulation, Clef, Dynamic, NoteDuration

__all__ = [
    "BarArg",
    "DurationArg",
    "EventIn",
    "PartIn",
    "ScoreArg",
    "ScoreIn",
    "StaffArg",
    "StaffIn",
    "VoiceIn",
    "score_dict",
]


def _values(enum: type[Enum]) -> str:
    """Return enum values as a comma-separated string for field descriptions."""
    return ", ".join(member.value for member in enum)


class EventIn(BaseModel):
    """Represent a single note, chord, or rest event in score input.

    Events are differentiated by pitch count: zero pitches indicate a rest,
    one indicates a single note, and two or more indicate a chord.
    """

    model_config = ConfigDict(extra="allow")

    pitches: list[str] | None = Field(
        default=None, description="Scientific pitch names, e.g. ['C4', 'F#5', 'Bb3']."
    )
    pitch: str | None = Field(default=None, description="Sugar for a single-pitch event.")
    kind: str | None = Field(
        default=None, description="'note', 'chord' or 'rest'. Inferred if absent."
    )
    duration: str | None = Field(default=None, description=f"One of: {_values(NoteDuration)}.")
    dots: int | None = Field(default=None, description="Rhythmic dots, 0 or more.")
    tie: bool | None = Field(default=None, description="Tie this event to the next.")
    articulations: list[str] | None = Field(
        default=None, description=f"Any of: {_values(Articulation)}."
    )
    dynamic: str | None = Field(default=None, description=f"One of: {_values(Dynamic)}.")
    slur: str | None = Field(default=None, description="'start' or 'stop'.")
    lyric: str | None = Field(default=None, description="Lyric syllable for this event.")
    staff: int | None = Field(default=None, description="0-based. Must match the nesting staff.")
    voice: int | None = Field(default=None, description="1-based. Must match the nesting voice.")


class VoiceIn(BaseModel):
    """Represent one rhythmic voice layer on a staff."""

    model_config = ConfigDict(extra="allow")

    events: list[EventIn] | None = None
    index: int | None = Field(
        default=None, description="1-based voice number, defaults to position."
    )


class StaffIn(BaseModel):
    """Represent one staff in a part, including clef and voices."""

    model_config = ConfigDict(extra="allow")

    clef: str | None = Field(default=None, description=f"One of: {_values(Clef)}.")
    voices: list[VoiceIn] | None = None


class PartIn(BaseModel):
    """Represent an instrument part with staves or flat event shortcuts."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(description="Required. The part name shown in the score.")
    instrument: str | None = None
    abbreviation: str | None = None
    staves: list[StaffIn] | None = Field(
        default=None, description="Canonical nested form. Mutually exclusive with 'events'."
    )
    events: list[EventIn] | None = Field(
        default=None, description="Flat shortcut for a single-staff, single-voice part."
    )


class ScoreIn(BaseModel):
    """Represent a complete score or flow definition."""

    model_config = ConfigDict(extra="allow")

    parts: list[PartIn] | None = None
    schema_version: str | None = None
    title: str | None = None
    composer: str | None = None
    lyricist: str | None = None
    key: str | None = Field(default=None, description="e.g. 'C major', 'd minor', 'Bb major'.")
    time: str | None = Field(default=None, description="e.g. '4/4', '3/4', '6/8'.")
    tempo: float | None = Field(default=None, description="Quarter-note BPM.")


# --------------------------------------------------------------------------- #
# Shared parameter annotations.
# --------------------------------------------------------------------------- #
# A description on the parameter reaches the client as
# ``inputSchema.properties.<name>.description``, which is where it looks for the
# units and the counting base before it reads any prose. The nested models above
# already carry theirs. These are the top-level arguments the tools share.
#
# Every enum-like argument in the server is normalised with ``.lower()``, so the
# accepted values are listed in prose rather than declared as a Literal. Narrowing
# the type would refuse "Quarter" and "Up", which the server accepts on purpose.

#: The ScoreSpec payload, shared by every tool that takes a whole score.
ScoreArg = Annotated[
    ScoreIn,
    Field(
        description=(
            "The score to work on, as a ScoreSpec object: metadata, parts, and the "
            "events inside them. Call score_schema first for the exact shape, which "
            "refuses unknown keys rather than ignoring them."
        )
    ),
]

#: A bar number, which Dorico counts from 1 rather than from 0.
BarArg = Annotated[
    int,
    Field(
        description=(
            "Bar number, counted from 1, so bar 1 is the first bar of the flow. "
            "Not an index."
        )
    ),
]

#: Whether the flow opens with a pickup bar, which Dorico leaves out of the count.
PickupArg = Annotated[
    bool,
    Field(
        description=(
            "True when the flow starts with a pickup (upbeat) bar. Dorico does not "
            "number it as bar 1, so bar navigation lands one bar short without "
            "this. Nothing in the API reveals a pickup, so ask the person whose "
            "score it is, or read an exported MusicXML file with read_score."
        )
    ),
]

#: A staff index, which is counted from 0, unlike a bar number.
StaffArg = Annotated[
    int,
    Field(
        description=(
            "Staff index, counted from 0, where 0 is the topmost staff in the "
            "current layout. Counted differently from a bar number on purpose: "
            "bars start at 1, staves at 0."
        )
    ),
]

#: A rhythmic duration name, matched case-insensitively against NoteDuration.
DurationArg = Annotated[
    str,
    Field(description=f"Rhythmic duration, one of: {_values(NoteDuration)}. Case-insensitive.")
]


def score_dict(score: ScoreIn | Mapping[str, Any] | Any) -> Any:
    """Convert a ScoreIn model or mapping into a dictionary for score normalization.

    Unset attributes are excluded to preserve defaults during downstream
    normalization in `score_from_dict`.
    """
    if isinstance(score, BaseModel):
        return score.model_dump(exclude_unset=True, mode="python")
    if isinstance(score, Mapping):
        return dict(score)
    if isinstance(score, Sequence) and not isinstance(score, (str, bytes)):
        return list(score)
    return score
