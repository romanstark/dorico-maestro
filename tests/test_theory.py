"""Tests for the extended music-theory analysis without Dorico.

Fixtures use unambiguous material and assert on confidence thresholds rather
than exact floats, since music21's key/roman labels can drift across versions
(pinned to music21 10.5.0 here).
"""

from __future__ import annotations

from typing import Any

import pytest

from dorico_maestro.models import InstrumentSpec
from dorico_maestro.music.score import ScoreSpec, score_from_dict
from dorico_maestro.music.theory import (
    check_ranges,
    check_species_counterpoint,
    check_voice_leading,
    detect_key,
    find_parallels,
    roman_numeral_analysis,
    suggest_cadence,
    suggest_next_chord,
)

C_MAJOR_SCALE = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"]


def _two_voice_spec(upper: list[str], lower: list[str]) -> ScoreSpec:
    """One part, two staves, one note-per-event voice each (for voice leading)."""
    return score_from_dict(
        {
            "parts": [
                {
                    "name": "P",
                    "staves": [
                        {"voices": [{"index": 1, "events": [{"pitch": p} for p in upper]}]},
                        {"voices": [{"index": 1, "events": [{"pitch": p} for p in lower]}]},
                    ],
                }
            ]
        }
    )


def _single_instrument_spec(instrument: str, pitches: list[str]) -> ScoreSpec:
    """One part with an instrument and a single voice of the given pitches."""
    return score_from_dict(
        {
            "parts": [
                {
                    "name": instrument,
                    "instrument": instrument,
                    "staves": [{"voices": [{"events": [{"pitch": p} for p in pitches]}]}],
                }
            ]
        }
    )


# --------------------------------------------------------------------------- #
# detect_key
# --------------------------------------------------------------------------- #


def test_detect_key_c_major_scale() -> None:
    result = detect_key(C_MAJOR_SCALE)
    assert result["tonic"] == "C"
    assert result["mode"] == "major"
    assert result["key"] == "C major"
    # Krumhansl correlation for a clean diatonic scale is strong.
    assert result["confidence"] is not None and result["confidence"] > 0.7
    assert result["alternatives"], "expected runner-up interpretations"
    assert all({"key", "confidence"} <= set(alt) for alt in result["alternatives"])


def test_detect_key_respects_top_n() -> None:
    assert len(detect_key(C_MAJOR_SCALE, top_n=2)["alternatives"]) == 2


def test_detect_key_from_score_spec() -> None:
    spec = score_from_dict(
        {
            "parts": [
                {
                    "name": "P",
                    "staves": [{"voices": [{"events": [{"pitch": p} for p in C_MAJOR_SCALE]}]}],
                }
            ]
        }
    )
    assert detect_key(spec)["key"] == "C major"


def test_detect_key_empty_source_is_all_none() -> None:
    result = detect_key([])
    assert result["key"] is None
    assert result["tonic"] is None
    assert result["alternatives"] == []


# --------------------------------------------------------------------------- #
# roman_numeral_analysis
# --------------------------------------------------------------------------- #


def test_roman_numeral_analysis_I_V_I() -> None:
    chords = [["C4", "E4", "G4"], ["G3", "B3", "D4"], ["C4", "E4", "G4"]]
    analysis = roman_numeral_analysis(chords, "C major")
    assert [item["roman"] for item in analysis] == ["I", "V", "I"]
    assert [item["function"] for item in analysis] == ["tonic", "dominant", "tonic"]
    assert [item["offset"] for item in analysis] == [0.0, 1.0, 2.0]


def test_roman_numeral_analysis_reports_inversion_figures() -> None:
    # ii6 (first inversion triad) and V7 (root-position seventh).
    analysis = roman_numeral_analysis([["F4", "A4", "D5"], ["G3", "B3", "D4", "F4"]], "C major")
    assert analysis[0]["roman"] == "ii6"
    assert analysis[0]["figured_bass"] == "6"
    assert analysis[0]["function"] == "subdominant"
    assert analysis[1]["roman"] == "V7"
    assert analysis[1]["figured_bass"] == "7"


def test_roman_numeral_analysis_from_score_spec() -> None:
    spec = score_from_dict(
        {
            "key": "C major",
            "time": "4/4",
            "parts": [
                {
                    "name": "P",
                    "staves": [
                        {
                            "voices": [
                                {
                                    "events": [
                                        {"pitches": ["C4", "E4", "G4"]},
                                        {"pitches": ["G3", "B3", "D4"]},
                                        {"pitches": ["C4", "E4", "G4"]},
                                        {"pitches": ["C4", "E4", "G4"]},
                                    ]
                                }
                            ]
                        }
                    ],
                }
            ],
        }
    )
    romans = [item["roman"] for item in roman_numeral_analysis(spec, "C major")]
    assert romans[0] == "I"
    assert "V" in romans


# --------------------------------------------------------------------------- #
# find_parallels
# --------------------------------------------------------------------------- #


def test_find_parallels_octave() -> None:
    parallels = find_parallels(["C4", "D4"], ["C3", "D3"])
    assert len(parallels) == 1
    assert parallels[0]["index"] == 1
    assert parallels[0]["type"] == "octave"
    assert parallels[0]["from"] == ["C4", "C3"]
    assert parallels[0]["to"] == ["D4", "D3"]


def test_find_parallels_fifth() -> None:
    parallels = find_parallels(["G4", "A4"], ["C4", "D4"])
    assert len(parallels) == 1
    assert parallels[0]["type"] == "fifth"
    assert parallels[0]["index"] == 1


def test_find_parallels_contrary_motion_is_clean() -> None:
    # Upper ascends, lower descends: no consecutive perfects.
    assert find_parallels(["G4", "A4"], ["C4", "B3"]) == []


def test_find_parallels_static_repeat_is_not_parallel() -> None:
    # Both voices hold the same fifth: oblique/static, not a parallel.
    assert find_parallels(["G4", "G4"], ["C4", "C4"]) == []


def test_find_parallels_interval_type_filter() -> None:
    # A parallel fifth is ignored when only octaves are requested.
    assert find_parallels(["G4", "A4"], ["C4", "D4"], interval_type="octave") == []
    assert find_parallels(["C4", "D4"], ["C3", "D3"], interval_type="fifth") == []


def test_find_parallels_unknown_interval_type_raises() -> None:
    with pytest.raises(ValueError, match="interval_type"):
        find_parallels(["C4"], ["C3"], interval_type="seventh")


# --------------------------------------------------------------------------- #
# check_voice_leading
# --------------------------------------------------------------------------- #


def test_check_voice_leading_flags_parallel_fifths() -> None:
    issues = check_voice_leading(_two_voice_spec(["G4", "A4"], ["C4", "D4"]))
    parallels = [i for i in issues if i["rule"] == "parallel-fifth"]
    assert len(parallels) == 1
    assert parallels[0]["severity"] == "error"
    assert parallels[0]["location"] == {"part": 0, "staff": 0, "voice": 1, "index": 1}


def test_check_voice_leading_clean_contrary_motion() -> None:
    assert check_voice_leading(_two_voice_spec(["G4", "F4"], ["C4", "D4"])) == []


def test_check_voice_leading_flags_oversized_leap() -> None:
    issues = check_voice_leading(_two_voice_spec(["C4", "E6"], ["C3", "D3"]))
    assert any(i["rule"] == "oversized-leap" and i["severity"] == "error" for i in issues)


# --------------------------------------------------------------------------- #
# suggest_cadence
# --------------------------------------------------------------------------- #


def test_suggest_cadence_major() -> None:
    assert suggest_cadence("C major", "authentic") == ["V", "I"]
    assert suggest_cadence("C major", "plagal") == ["IV", "I"]
    assert suggest_cadence("C major", "half") == ["IV", "V"]
    assert suggest_cadence("C major", "deceptive") == ["V", "vi"]


def test_suggest_cadence_minor() -> None:
    assert suggest_cadence("a minor", "authentic") == ["V", "i"]
    assert suggest_cadence("a minor", "plagal") == ["iv", "i"]


def test_suggest_cadence_default_is_authentic() -> None:
    assert suggest_cadence("C major") == ["V", "I"]


def test_suggest_cadence_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="cadence kind"):
        suggest_cadence("C major", "cadenza")


# --------------------------------------------------------------------------- #
# suggest_next_chord
# --------------------------------------------------------------------------- #


def test_suggest_next_chord_after_dominant_resolves_to_tonic() -> None:
    suggestions = suggest_next_chord("C major", ["I", "IV", "V"])
    assert suggestions[0]["roman"] == "I"
    assert suggestions[0]["cadential"] is True
    # The deceptive resolution to vi is also offered.
    assert any(s["roman"] == "vi" and s["cadential"] for s in suggestions)


def test_suggest_next_chord_empty_progression_starts_on_tonic() -> None:
    assert suggest_next_chord("C major", [])[0]["roman"] == "I"


def test_suggest_next_chord_respects_n() -> None:
    assert len(suggest_next_chord("C major", ["V"], n=1)) == 1


def test_suggest_next_chord_minor_mode_spelling() -> None:
    # After a minor tonic, the subdominant suggestion is lower-case iv.
    romans = {s["roman"] for s in suggest_next_chord("a minor", ["i"])}
    assert "iv" in romans


# --------------------------------------------------------------------------- #
# check_ranges
# --------------------------------------------------------------------------- #


def test_check_ranges_flags_note_below_instrument() -> None:
    issues = check_ranges(_single_instrument_spec("Piccolo", ["C4"]))
    assert len(issues) == 1
    assert issues[0]["direction"] == "below"
    assert issues[0]["pitch"] == "C4"
    assert issues[0]["instrument"] == "Piccolo"
    assert issues[0]["location"] == {"part": 0, "staff": 0, "voice": 1, "index": 0}


def test_check_ranges_passes_in_range_note() -> None:
    assert check_ranges(_single_instrument_spec("Violin", ["A3"])) == []


def test_check_ranges_respects_instrument_spec_override() -> None:
    spec = _single_instrument_spec("Violin", ["A3"])
    override = {"Violin": InstrumentSpec(name="Violin", lowest="C4")}
    issues = check_ranges(spec, instruments=override)
    assert len(issues) == 1
    assert issues[0]["direction"] == "below"


def test_check_ranges_skips_parts_without_instrument() -> None:
    spec = score_from_dict(
        {"parts": [{"name": "P", "staves": [{"voices": [{"events": [{"pitch": "C0"}]}]}]}]}
    )
    assert check_ranges(spec) == []


# --------------------------------------------------------------------------- #
# check_species_counterpoint
# --------------------------------------------------------------------------- #


def test_check_species_counterpoint_clean_exercise() -> None:
    cantus = ["C4", "D4", "E4", "F4", "G4"]
    counterpoint = ["G4", "F4", "C5", "A4", "G4"]
    assert check_species_counterpoint(cantus, counterpoint) == []


def test_check_species_counterpoint_flags_parallel_octaves() -> None:
    issues = check_species_counterpoint(["C4", "D4"], ["C5", "D5"])
    rules = {issue["rule"] for issue in issues}
    assert "parallel-octave" in rules


def test_check_species_counterpoint_flags_imperfect_opening() -> None:
    # Begins on a third (imperfect); not a legal first-species opening.
    issues = check_species_counterpoint(["C4", "D4"], ["E4", "D5"])
    assert any(issue["rule"] == "opening" for issue in issues)


def test_check_species_counterpoint_flags_dissonance() -> None:
    # C4/D4 is a major second: a dissonant downbeat.
    issues = check_species_counterpoint(["C4", "C4"], ["C4", "D4"])
    assert any(issue["rule"] == "dissonance" for issue in issues)


def test_check_species_counterpoint_second_species_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        check_species_counterpoint(["C4"], ["C5"], species=2)


def test_check_species_counterpoint_returns_json_friendly_dicts() -> None:
    issues: list[dict[str, Any]] = check_species_counterpoint(["C4", "D4"], ["C5", "D5"])
    assert all(set(issue) == {"index", "rule", "message"} for issue in issues)
