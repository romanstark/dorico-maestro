"""Tests for the score-spec model and its music21 bridge without Dorico."""

from __future__ import annotations

from typing import Any

import pytest
from music21 import stream

from dorico_maestro.models import Articulation, Clef, Dynamic, NoteDuration
from dorico_maestro.music.score import (
    Event,
    ScoreSpec,
    ScoreSpecError,
    Voice,
    music21_to_score,
    score_from_dict,
    score_to_dict,
    score_to_music21,
    spec_schema,
    total_events,
    validate,
)


def worked_example() -> dict[str, Any]:
    """The golden fixture: two bars, two-staff piano, C major 4/4."""
    return {
        "schema_version": "1.0",
        "title": "Two-Bar Sketch",
        "composer": "Dorico Maestro",
        "key": "C major",
        "time": "4/4",
        "tempo": 88,
        "parts": [
            {
                "name": "Piano",
                "instrument": "Piano",
                "staves": [
                    {
                        "clef": "treble",
                        "voices": [
                            {
                                "index": 1,
                                "events": [
                                    {"pitches": ["E4"], "duration": "quarter", "dynamic": "mp"},
                                    {"pitches": ["G4"], "duration": "quarter"},
                                    {
                                        "pitches": ["C5"],
                                        "duration": "quarter",
                                        "articulations": ["staccato"],
                                    },
                                    {"pitches": ["B4"], "duration": "quarter"},
                                    {"pitches": ["C5", "E5", "G5"], "duration": "half"},
                                    {"pitches": [], "duration": "half"},
                                ],
                            }
                        ],
                    },
                    {
                        "clef": "bass",
                        "voices": [
                            {
                                "index": 1,
                                "events": [
                                    {"pitches": ["C3"], "duration": "half"},
                                    {"pitches": ["G2"], "duration": "half"},
                                    {"pitches": ["C2"], "duration": "whole"},
                                ],
                            }
                        ],
                    },
                ],
            }
        ],
    }


# --------------------------------------------------------------------------- #
# Model dataclasses
# --------------------------------------------------------------------------- #


def test_event_kind_properties() -> None:
    assert Event().is_rest is True
    assert Event(pitches=["C4"]).is_rest is False
    assert Event(pitches=["C4"]).is_chord is False
    assert Event(pitches=["C4", "E4"]).is_chord is True


def test_event_quarter_length_with_dots() -> None:
    assert Event(pitches=["C4"], duration=NoteDuration.QUARTER).quarter_length == 1.0
    assert Event(pitches=["C4"], duration=NoteDuration.QUARTER, dots=1).quarter_length == 1.5
    assert Event(pitches=["C4"], duration=NoteDuration.HALF, dots=2).quarter_length == 3.5


def test_voice_quarter_length_is_float() -> None:
    empty = Voice()
    assert empty.quarter_length == 0.0
    assert isinstance(empty.quarter_length, float)
    voice = Voice(events=[Event(pitches=["C4"]), Event(pitches=["D4"], duration=NoteDuration.HALF)])
    assert voice.quarter_length == 3.0


# --------------------------------------------------------------------------- #
# dict <-> model round-trip
# --------------------------------------------------------------------------- #


def test_round_trip_worked_example() -> None:
    spec = score_from_dict(worked_example())
    assert score_from_dict(score_to_dict(spec)) == spec


def test_loader_populates_staff_and_voice() -> None:
    spec = score_from_dict(worked_example())
    treble = spec.parts[0].staves[0].voices[0].events
    bass = spec.parts[0].staves[1].voices[0].events
    assert (treble[0].staff, treble[0].voice) == (0, 1)
    assert (bass[0].staff, bass[0].voice) == (1, 1)


def test_flat_form_normalizes_to_nested() -> None:
    flat = {
        "parts": [
            {
                "name": "Piano",
                "events": [
                    {"pitch": "C4", "duration": "quarter", "staff": 0, "voice": 1},
                    {"pitch": "E4", "duration": "quarter", "staff": 0, "voice": 1},
                    {"pitch": "C2", "duration": "half", "staff": 1, "voice": 1},
                ],
            }
        ]
    }
    nested = {
        "parts": [
            {
                "name": "Piano",
                "staves": [
                    {
                        "voices": [
                            {
                                "index": 1,
                                "events": [
                                    {"pitch": "C4", "duration": "quarter"},
                                    {"pitch": "E4", "duration": "quarter"},
                                ],
                            }
                        ]
                    },
                    {"voices": [{"index": 1, "events": [{"pitch": "C2", "duration": "half"}]}]},
                ],
            }
        ]
    }
    assert score_from_dict(flat) == score_from_dict(nested)


def test_flat_form_creates_missing_intermediate_staff() -> None:
    # Events on staff 0 and staff 2 -> staff 1 must exist (empty).
    spec = score_from_dict(
        {
            "parts": [
                {
                    "name": "Organ",
                    "events": [
                        {"pitch": "C5", "staff": 0},
                        {"pitch": "C2", "staff": 2},
                    ],
                }
            ]
        }
    )
    staves = spec.parts[0].staves
    assert len(staves) == 3
    assert staves[1].voices[0].events == []


def test_pitch_sugar_and_rest_tokens() -> None:
    spec = score_from_dict(
        {
            "parts": [
                {
                    "name": "P",
                    "staves": [
                        {
                            "voices": [
                                {
                                    "events": [
                                        {"pitch": "F#5"},
                                        {"pitch": "rest"},
                                        {"pitch": None},
                                        {"pitches": []},
                                    ]
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    )
    events = spec.parts[0].staves[0].voices[0].events
    assert events[0].pitches == ["F#5"]
    assert all(e.is_rest for e in events[1:])


def test_default_voice_index_from_position() -> None:
    spec = score_from_dict(
        {
            "parts": [
                {
                    "name": "P",
                    "staves": [
                        {"voices": [{"events": []}, {"events": []}]},
                    ],
                }
            ]
        }
    )
    voices = spec.parts[0].staves[0].voices
    assert [v.index for v in voices] == [1, 2]


def test_score_to_dict_emits_nested_without_flat_keys() -> None:
    spec = score_from_dict(worked_example())
    data = score_to_dict(spec)
    event = data["parts"][0]["staves"][0]["voices"][0]["events"][0]
    # Canonical events never carry the flat staff/voice shortcut.
    assert "staff" not in event
    assert "voice" not in event
    assert event["pitches"] == ["E4"]


def test_lyricist_round_trips_dict_and_music21() -> None:
    data = worked_example()
    data["composer"] = "A. Composer"
    data["lyricist"] = "P. Poet"
    spec = score_from_dict(data)
    assert spec.lyricist == "P. Poet"
    # dict round-trip preserves it
    assert score_to_dict(spec)["lyricist"] == "P. Poet"
    assert score_from_dict(score_to_dict(spec)) == spec
    # and it survives the music21 bridge both ways
    m21 = score_to_music21(spec)
    assert m21.metadata.lyricist == "P. Poet"
    assert music21_to_score(m21).lyricist == "P. Poet"


# --------------------------------------------------------------------------- #
# Validation errors (each path-tagged)
# --------------------------------------------------------------------------- #


def test_empty_parts_rejected() -> None:
    with pytest.raises(ScoreSpecError, match="at least one part"):
        score_from_dict({"parts": []})


def test_bad_schema_major_rejected() -> None:
    with pytest.raises(ScoreSpecError, match="major version"):
        score_from_dict({"schema_version": "2.0", "parts": [{"name": "P"}]})


def test_unknown_duration_rejected() -> None:
    with pytest.raises(ScoreSpecError) as excinfo:
        score_from_dict(
            {"parts": [{"name": "P", "staves": [{"voices": [
                {"events": [{"pitch": "C4", "duration": "triplet"}]}]}]}]}
        )
    assert "events[0].duration" in str(excinfo.value)


def test_unknown_articulation_rejected() -> None:
    with pytest.raises(ScoreSpecError) as excinfo:
        score_from_dict(
            {
                "parts": [
                    {
                        "name": "P",
                        "staves": [
                            {"voices": [{"events": [{"pitch": "C4", "articulations": ["wobble"]}]}]}
                        ],
                    }
                ]
            }
        )
    assert "articulations[0]" in str(excinfo.value)


def test_bad_pitch_rejected_with_path() -> None:
    with pytest.raises(ScoreSpecError) as excinfo:
        score_from_dict(
            {"parts": [{"name": "P", "staves": [{"voices": [{"events": [{"pitches": ["H9"]}]}]}]}]}
        )
    assert "events[0]" in str(excinfo.value)


def test_dots_out_of_range_rejected() -> None:
    with pytest.raises(ScoreSpecError, match="between 0 and 2"):
        score_from_dict(
            {"parts": [{"name": "P", "staves": [{"voices": [
                {"events": [{"pitch": "C4", "dots": 3}]}]}]}]}
        )


def test_staff_voice_mismatch_under_nesting_rejected() -> None:
    with pytest.raises(ScoreSpecError) as excinfo:
        score_from_dict(
            {
                "parts": [
                    {
                        "name": "P",
                        "staves": [
                            {"voices": [{"index": 1, "events": [{"pitch": "C4", "voice": 2}]}]}
                        ],
                    }
                ]
            }
        )
    assert "disagrees with nesting voice" in str(excinfo.value)


def test_kind_disagreeing_with_pitches_rejected() -> None:
    with pytest.raises(ScoreSpecError, match="chord needs >=2 pitches"):
        score_from_dict(
            {"parts": [{"name": "P", "staves": [{"voices": [
                {"events": [{"pitches": ["C4"], "kind": "chord"}]}]}]}]}
        )


def test_both_staves_and_events_rejected() -> None:
    with pytest.raises(ScoreSpecError, match="not both"):
        score_from_dict({"parts": [{"name": "P", "staves": [], "events": []}]})


def test_missing_part_name_rejected() -> None:
    with pytest.raises(ScoreSpecError, match="'name' is required"):
        score_from_dict({"parts": [{"instrument": "Piano"}]})


# --------------------------------------------------------------------------- #
# Non-raising validate()
# --------------------------------------------------------------------------- #


def test_validate_clean_on_worked_example() -> None:
    assert validate(score_from_dict(worked_example())) == []


def test_validate_warns_on_incomplete_bar() -> None:
    spec = ScoreSpec(
        time="4/4",
        parts=score_from_dict(
            {"parts": [{"name": "P", "staves": [{"voices": [{"events": [{"pitch": "C4"}]}]}]}]}
        ).parts,
    )
    problems = validate(spec)
    assert any("whole number" in p for p in problems)


# --------------------------------------------------------------------------- #
# model -> music21
# --------------------------------------------------------------------------- #


def test_score_to_music21_structure_and_counts() -> None:
    spec = score_from_dict(worked_example())
    score = score_to_music21(spec)

    # Grand staff -> two PartStaff streams.
    assert len(score.parts) == 2
    assert all(isinstance(p, stream.PartStaff) for p in score.parts)

    treble, bass = score.parts[0], score.parts[1]
    assert len(list(treble.getElementsByClass(stream.Measure))) == 2
    assert len(list(bass.getElementsByClass(stream.Measure))) == 2
    # Treble: 4 notes + 1 chord object (rest excluded from .notes) = 5; bass = 3.
    assert len(list(treble.recurse().notes)) == 5
    assert len(list(bass.recurse().notes)) == 3

    # Total sounding length per staff is two 4/4 bars.
    assert treble.duration.quarterLength == pytest.approx(8.0)
    assert bass.duration.quarterLength == pytest.approx(8.0)


def test_score_to_music21_applies_globals() -> None:
    from music21 import key as key_mod
    from music21 import meter, tempo

    score = score_to_music21(score_from_dict(worked_example()))
    assert score.metadata.title == "Two-Bar Sketch"
    keys = list(score.recurse().getElementsByClass(key_mod.Key))
    assert keys and keys[0].name == "C major"
    times = list(score.recurse().getElementsByClass(meter.TimeSignature))
    assert times and times[0].ratioString == "4/4"
    marks = list(score.recurse().getElementsByClass(tempo.MetronomeMark))
    assert marks and marks[0].getQuarterBPM() == pytest.approx(88.0)


def test_score_to_music21_sharp_and_chord() -> None:
    spec = score_from_dict(
        {
            "parts": [
                {
                    "name": "P",
                    "staves": [
                        {
                            "voices": [
                                {
                                    "events": [
                                        {"pitch": "F#5", "duration": "quarter"},
                                        {"pitches": ["C4", "E4", "G4"], "duration": "quarter"},
                                    ]
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    )
    notes = list(score_to_music21(spec).parts[0].recurse().notes)
    assert notes[0].nameWithOctave == "F#5"
    assert [p.nameWithOctave for p in notes[1].pitches] == ["C4", "E4", "G4"]


# --------------------------------------------------------------------------- #
# music21 -> model (best-effort inverse)
# --------------------------------------------------------------------------- #


def test_music21_round_trip_preserves_content() -> None:
    spec = score_from_dict(worked_example())
    recovered = music21_to_score(score_to_music21(spec))

    # Grand staff comes back as two separate single-staff parts (best-effort).
    assert len(recovered.parts) == 2
    assert recovered.key == "C major"
    assert recovered.time == "4/4"
    assert recovered.tempo == pytest.approx(88.0)

    treble = recovered.parts[0].staves[0].voices[0].events
    assert [e.pitches for e in treble] == [
        ["E4"],
        ["G4"],
        ["C5"],
        ["B4"],
        ["C5", "E5", "G5"],
        [],
    ]
    assert [e.duration for e in treble] == [
        NoteDuration.QUARTER,
        NoteDuration.QUARTER,
        NoteDuration.QUARTER,
        NoteDuration.QUARTER,
        NoteDuration.HALF,
        NoteDuration.HALF,
    ]
    # Clef, articulation and dynamic survive the round-trip.
    assert recovered.parts[0].staves[0].clef is Clef.TREBLE
    assert Articulation.STACCATO in treble[2].articulations
    assert treble[0].dynamic is Dynamic.MP


def test_music21_round_trip_preserves_dots_and_ties() -> None:
    spec = score_from_dict(
        {
            "time": "4/4",
            "parts": [
                {
                    "name": "P",
                    "staves": [
                        {
                            "voices": [
                                {
                                    "events": [
                                        {"pitch": "C4", "duration": "quarter", "dots": 1},
                                        {"pitch": "C4", "duration": "eighth"},
                                        {"pitch": "D4", "duration": "half", "tie": True},
                                        {"pitch": "D4", "duration": "half"},
                                    ]
                                }
                            ]
                        }
                    ],
                }
            ],
        }
    )
    events = music21_to_score(score_to_music21(spec)).parts[0].staves[0].voices[0].events
    assert events[0].duration is NoteDuration.QUARTER
    assert events[0].dots == 1
    assert events[2].tie is True
    assert events[3].tie is False


def test_music21_to_score_never_raises_on_empty_score() -> None:
    result = music21_to_score(stream.Score())
    assert isinstance(result, ScoreSpec)
    assert result.parts  # a placeholder part is provided


# --------------------------------------------------------------------------- #
# strict keys, event counting and the schema helper
# --------------------------------------------------------------------------- #


def test_score_from_dict_rejects_unknown_part_key() -> None:
    # 'notes' instead of 'events' must fail loudly (not silently make an empty part).
    with pytest.raises(ScoreSpecError, match="notes"):
        score_from_dict({"parts": [{"name": "Piano", "notes": [{"pitch": "C4"}]}]})


def test_score_from_dict_rejects_unknown_event_key() -> None:
    with pytest.raises(ScoreSpecError, match="dur"):
        score_from_dict({"parts": [{"name": "P", "events": [{"pitch": "C4", "dur": "quarter"}]}]})


def test_total_events_counts_notes_and_rests() -> None:
    spec = score_from_dict(
        {"parts": [{"name": "P", "events": [{"pitch": "C4"}, {"pitch": "D4"}, {"pitches": []}]}]}
    )
    assert total_events(spec) == 3


def test_total_events_zero_for_empty_part() -> None:
    spec = score_from_dict({"parts": [{"name": "Piano"}]})
    assert total_events(spec) == 0


def test_spec_schema_has_examples_and_enums() -> None:
    schema = spec_schema()
    assert "minimal_flat" in schema and "minimal_nested" in schema
    assert "quarter" in schema["enums"]["duration"]
    assert "staccato" in schema["enums"]["articulation"]
    # The advertised example is itself a valid, non-empty spec.
    assert total_events(score_from_dict(schema["minimal_flat"])) > 0
    assert total_events(score_from_dict(schema["minimal_nested"])) > 0
