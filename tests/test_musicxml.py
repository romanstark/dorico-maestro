"""Tests for the ScoreSpec <-> MusicXML bridge.

Everything here is offline: files are written under pytest's ``tmp_path`` and read
back through :mod:`music21`; nothing opens a socket or touches a real Dorico.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dorico_maestro.music.musicxml import (
    musicxml_to_score,
    parse_musicxml,
    score_to_musicxml,
)
from dorico_maestro.music.score import score_from_dict

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

# One flute line whose events fill exactly two 4/4 bars, so makeTies never splits
# a note across a barline (which would change the recovered event structure).
#   bar 1: C4 q + D4 q + E4 half              = 1 + 1 + 2   = 4.0
#   bar 2: F4 dotted-quarter + G4 eighth + A4 half = 1.5 + 0.5 + 2 = 4.0
_MELODY_SPEC: dict = {
    "schema_version": "1.0",
    "title": "Round Trip",
    "composer": "Tester",
    "time": "4/4",
    "tempo": 96,
    "parts": [
        {
            "name": "Flute",
            "instrument": "Flute",
            "staves": [
                {
                    "clef": "treble",
                    "voices": [
                        {
                            "index": 1,
                            "events": [
                                {"pitches": ["C4"], "duration": "quarter"},
                                {"pitches": ["D4"], "duration": "quarter"},
                                {"pitches": ["E4"], "duration": "half"},
                                {"pitches": ["F4"], "duration": "quarter", "dots": 1},
                                {"pitches": ["G4"], "duration": "eighth"},
                                {"pitches": ["A4"], "duration": "half"},
                            ],
                        }
                    ],
                }
            ],
        }
    ],
}

# Two-staff piano (grand staff), C major 4/4 — the worked example from the contract.
_PIANO_SPEC: dict = {
    "schema_version": "1.0",
    "title": "Two-Bar Sketch",
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
                                {"pitches": ["E4"], "duration": "quarter"},
                                {"pitches": ["G4"], "duration": "quarter"},
                                {"pitches": ["C5"], "duration": "quarter",
                                 "articulations": ["staccato"]},
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
# ScoreSpec -> MusicXML -> ScoreSpec round-trip
# --------------------------------------------------------------------------- #


def test_score_to_musicxml_writes_file(tmp_path: Path) -> None:
    spec = score_from_dict(_MELODY_SPEC)
    # A nested output path also exercises parent-directory creation.
    out = tmp_path / "nested" / "melody.musicxml"
    written = score_to_musicxml(spec, out)
    assert Path(written).exists()


def test_round_trip_preserves_pitches_durations_dots(tmp_path: Path) -> None:
    spec = score_from_dict(_MELODY_SPEC)
    written = score_to_musicxml(spec, tmp_path / "melody.musicxml")

    recovered = musicxml_to_score(written)
    # Composer round-trips through score.py's music21_to_score; the notated title
    # is verified via the trusted parser in test_round_trip_via_trusted_parser
    # (music21 re-parses <work-title> into bestTitle, not metadata.title).
    assert recovered.composer == "Tester"
    assert recovered.time == "4/4"
    assert recovered.tempo == pytest.approx(96.0)
    assert len(recovered.parts) == 1

    part = recovered.parts[0]
    assert part.name == "Flute"
    assert len(part.staves) == 1
    assert len(part.staves[0].voices) == 1

    events = part.staves[0].voices[0].events
    assert [e.pitches[0] for e in events] == ["C4", "D4", "E4", "F4", "G4", "A4"]
    assert [e.duration.value for e in events] == [
        "quarter", "quarter", "half", "quarter", "eighth", "half",
    ]
    assert [e.dots for e in events] == [0, 0, 0, 1, 0, 0]


def test_round_trip_via_trusted_parser(tmp_path: Path) -> None:
    # The written file agrees with the independent parse_musicxml summary.
    spec = score_from_dict(_MELODY_SPEC)
    written = score_to_musicxml(spec, tmp_path / "melody.musicxml")

    summary = parse_musicxml(written)
    assert summary["title"] == "Round Trip"
    assert summary["part_count"] == 1
    assert summary["note_count"] == 6
    assert summary["time_signature"] == "4/4"
    assert summary["tempo_bpm"] == pytest.approx(96.0)


def test_grand_staff_survives(tmp_path: Path) -> None:
    # A two-staff piano writes as a grand staff and re-parses to the right note count
    # (chord counts as one note object; the rest is excluded): treble 4+chord = 5,
    # bass 3 -> 8, regardless of how music21 splits the grand staff on re-import.
    spec = score_from_dict(_PIANO_SPEC)
    written = score_to_musicxml(spec, tmp_path / "piano.musicxml")

    summary = parse_musicxml(written)
    assert summary["note_count"] == 8
    assert "C" in (summary["key"] or "")
    assert summary["time_signature"] == "4/4"


def test_path_with_spaces_and_ampersand(tmp_path: Path) -> None:
    # music21 must handle awkward filenames; the write + read round-trips.
    spec = score_from_dict(_MELODY_SPEC)
    out = tmp_path / "my scores" / "piece & sketch.musicxml"
    written = score_to_musicxml(spec, out)
    assert Path(written).exists()
    summary = parse_musicxml(written)
    assert summary["note_count"] == 6


def test_musicxml_to_score_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        musicxml_to_score(tmp_path / "does_not_exist.musicxml")


