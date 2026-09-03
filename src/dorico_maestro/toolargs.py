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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dorico_maestro.models import Articulation, Clef, Dynamic, NoteDuration

__all__ = ["EventIn", "PartIn", "ScoreIn", "StaffIn", "VoiceIn", "score_dict"]


def _values(enum: type) -> str:
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
        default=None, description="'note', 'chord' or 'rest'; inferred if absent."
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
    staff: int | None = Field(default=None, description="0-based; must match the nesting staff.")
    voice: int | None = Field(default=None, description="1-based; must match the nesting voice.")


class VoiceIn(BaseModel):
    """Represent one rhythmic voice layer on a staff."""

    model_config = ConfigDict(extra="allow")

    events: list[EventIn] | None = None
    index: int | None = Field(
        default=None, description="1-based voice number; defaults to position."
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
