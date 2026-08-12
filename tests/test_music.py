"""Tests for the music21 layer: MusicXML round-trip and theory helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from dorico_maestro.music.musicxml import generate_musicxml, parse_musicxml
from dorico_maestro.music.theory import (
    analyze_chord,
    note_in_range,
    parse_key,
    suggest_progression,
)

# --------------------------------------------------------------------------- #
# MusicXML round-trip
# --------------------------------------------------------------------------- #


def test_generate_and_parse_single_part(tmp_path: Path) -> None:
    out = tmp_path / "melody.musicxml"
    # Four quarter notes exactly fill one 4/4 bar (no tied split across a barline).
    written = generate_musicxml(
        ["C4", "D4", "E4", "F4"],
        out,
        title="Test Melody",
        tempo_bpm=120,
        time_signature="4/4",
        key="C major",
    )
    assert Path(written).exists()

    summary = parse_musicxml(written)
    assert summary["title"] == "Test Melody"
    assert summary["part_count"] == 1
    assert summary["note_count"] == 4
    assert summary["time_signature"] == "4/4"
    assert summary["tempo_bpm"] == pytest.approx(120.0)
    assert summary["parts"][0]["name"] == "Melody"


def test_generate_and_parse_multi_part(tmp_path: Path) -> None:
    out = tmp_path / "duet.musicxml"
    generate_musicxml(
        {
            "Melody": ["C4", "D4", ("E4", "half")],
            "Bass": [("C3", "whole")],
        },
        out,
        time_signature="4/4",
    )
    summary = parse_musicxml(out)
    assert summary["part_count"] == 2
    names = {p["name"] for p in summary["parts"]}
    assert names == {"Melody", "Bass"}
    # Total real notes across both parts: 3 + 1.
    assert summary["note_count"] == 4


def test_parse_reports_ambitus(tmp_path: Path) -> None:
    out = tmp_path / "range.musicxml"
    generate_musicxml(["C4", "G4", "C5"], out)
    summary = parse_musicxml(out)
    ambitus = summary["parts"][0]["ambitus"]
    assert ambitus is not None
    assert ambitus["lowest"] == "C4"
    assert ambitus["highest"] == "C5"
    assert ambitus["range_semitones"] == 12


def test_chord_and_rest_specs(tmp_path: Path) -> None:
    out = tmp_path / "chords.musicxml"
    generate_musicxml(
        [(["C4", "E4", "G4"], "half"), "rest", "C4"],
        out,
    )
    summary = parse_musicxml(out)
    # Chord counts as one note object + one actual note = 2 (rest excluded).
    assert summary["note_count"] == 2


def test_parse_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_musicxml(tmp_path / "does_not_exist.musicxml")


# --------------------------------------------------------------------------- #
# Theory helpers
# --------------------------------------------------------------------------- #


def test_analyze_c_major_triad() -> None:
    info = analyze_chord(["C4", "E4", "G4"])
    assert info["root"] == "C"
    assert info["symbol"] == "C"
    assert info["cardinality"] == 3
    assert info["quality"] == "major"


def test_analyze_minor_seventh() -> None:
    info = analyze_chord(["C", "Eb", "G", "Bb"])
    assert info["root"] == "C"
    assert info["symbol"] == "Cm7"


def test_analyze_empty_raises() -> None:
    with pytest.raises(ValueError):
        analyze_chord([])


def test_suggest_progression_major() -> None:
    assert suggest_progression("C major", 4) == ["I", "IV", "V", "I"]


def test_suggest_progression_minor() -> None:
    assert suggest_progression("a minor", 4) == ["i", "iv", "V", "i"]


def test_suggest_progression_invalid_length() -> None:
    with pytest.raises(ValueError):
        suggest_progression("C major", 0)


def test_parse_key_conventions() -> None:
    assert parse_key("C").mode == "major"
    assert parse_key("a").mode == "minor"
    assert parse_key("Eb major").tonic.name == "E-"
    assert parse_key("F# minor").mode == "minor"


def test_note_in_range() -> None:
    assert note_in_range("violin", "A3") is True
    assert note_in_range("piccolo", "C4") is False


def test_note_in_range_unknown_instrument() -> None:
    with pytest.raises(ValueError):
        note_in_range("kazoo-o-tron-9000", "C4")
