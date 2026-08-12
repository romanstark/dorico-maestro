"""Tests for the pure value types and maps added for the score/render layer.

Offline only: ``models`` must stay pure stdlib (no music21, no sockets), so
these tests assert both the data content and that purity.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from dorico_maestro.models import (
    ARTICULATION_TO_DORICO,
    ARTICULATION_TO_MUSIC21,
    CLEF_TO_MUSIC21,
    DURATION_FROM_MUSIC21,
    DURATION_QUARTER_LENGTH,
    DURATION_TO_MUSIC21,
    Articulation,
    Clef,
    Dynamic,
    InstrumentSpec,
    NoteDuration,
    TimeSignature,
    dotted_multiplier,
)


# ----------------------------------------------------------------- purity guard
def test_models_import_does_not_pull_in_music21() -> None:
    # Importing models must not drag music21 into sys.modules: models is the pure
    # stdlib base of the import graph. Import in a clean subprocess so a music21
    # already imported by another test can't mask a regression.
    import subprocess
    import sys

    code = (
        "import sys; import dorico_maestro.models; "
        "assert 'music21' not in sys.modules, 'models pulled in music21'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------- Dynamic
def test_dynamic_is_str_enum_with_expected_values() -> None:
    assert Dynamic.MP == "mp"
    assert Dynamic.SFZ.value == "sfz"
    assert {d.value for d in Dynamic} == {
        "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "sf", "sfz", "fp", "rf",
    }


# ----------------------------------------------------------------- Articulation
def test_every_articulation_has_a_dorico_value() -> None:
    for art in Articulation:
        assert art in ARTICULATION_TO_DORICO
        assert ARTICULATION_TO_DORICO[art].startswith("k")


def test_every_articulation_has_a_music21_class_name() -> None:
    for art in Articulation:
        assert art in ARTICULATION_TO_MUSIC21
        assert ARTICULATION_TO_MUSIC21[art]  # non-empty class name


def test_articulation_dorico_specific_values() -> None:
    assert ARTICULATION_TO_DORICO[Articulation.STACCATO_TENUTO] == "kStaccatoTenuto"
    assert ARTICULATION_TO_MUSIC21[Articulation.MARCATO] == "StrongAccent"


# ----------------------------------------------------------------------- Clef
def test_every_clef_has_a_music21_class_name() -> None:
    for clef in Clef:
        assert clef in CLEF_TO_MUSIC21
        assert CLEF_TO_MUSIC21[clef].endswith("Clef")


def test_clef_specific_values() -> None:
    assert CLEF_TO_MUSIC21[Clef.TREBLE_8VB] == "Treble8vbClef"
    assert CLEF_TO_MUSIC21[Clef.PERCUSSION] == "PercussionClef"


# ---------------------------------------------------------------- duration maps
def test_duration_quarter_length_covers_every_note_value() -> None:
    for dur in NoteDuration:
        assert dur in DURATION_QUARTER_LENGTH
    assert DURATION_QUARTER_LENGTH[NoteDuration.WHOLE] == 4.0
    assert DURATION_QUARTER_LENGTH[NoteDuration.QUARTER] == 1.0
    assert DURATION_QUARTER_LENGTH[NoteDuration.SIXTY_FOURTH] == 0.0625


def test_duration_to_music21_uses_16th_spelling() -> None:
    # Our value is "sixteenth" but music21's duration.type is "16th".
    assert NoteDuration.SIXTEENTH.value == "sixteenth"
    assert DURATION_TO_MUSIC21[NoteDuration.SIXTEENTH] == "16th"


def test_duration_to_music21_covers_every_note_value() -> None:
    for dur in NoteDuration:
        assert dur in DURATION_TO_MUSIC21


def test_duration_from_music21_round_trips() -> None:
    for dur in NoteDuration:
        assert DURATION_FROM_MUSIC21[DURATION_TO_MUSIC21[dur]] == dur
    assert DURATION_FROM_MUSIC21["16th"] == NoteDuration.SIXTEENTH


# ------------------------------------------------------------ dotted_multiplier
@pytest.mark.parametrize(
    ("dots", "expected"),
    [(0, 1.0), (1, 1.5), (2, 1.75)],
)
def test_dotted_multiplier(dots: int, expected: float) -> None:
    assert dotted_multiplier(dots) == expected


def test_dotted_multiplier_negative_raises() -> None:
    with pytest.raises(ValueError):
        dotted_multiplier(-1)


# --------------------------------------------------------------- TimeSignature
def test_time_signature_parse_simple() -> None:
    ts = TimeSignature.parse("6/8")
    assert (ts.numerator, ts.denominator) == (6, 8)
    assert ts.ratio_string == "6/8"
    assert ts.bar_quarter_length == 3.0


def test_time_signature_four_four_bar_length() -> None:
    assert TimeSignature.parse("4/4").bar_quarter_length == 4.0


def test_time_signature_is_frozen() -> None:
    ts = TimeSignature(3, 4)
    with pytest.raises(FrozenInstanceError):
        ts.numerator = 4  # type: ignore[misc]


@pytest.mark.parametrize("bad", ["", "4", "4/", "/4", "4/4/4", "x/4", "4/y", "0/4", "4/0", "-3/4"])
def test_time_signature_parse_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        TimeSignature.parse(bad)


# --------------------------------------------------------------- InstrumentSpec
def test_instrument_spec_defaults() -> None:
    spec = InstrumentSpec(name="Piano")
    assert spec.name == "Piano"
    assert spec.abbreviation is None
    assert spec.midi_program is None
    assert spec.lowest is None
    assert spec.highest is None


def test_instrument_spec_with_range() -> None:
    spec = InstrumentSpec(name="Cello", abbreviation="Vc.", midi_program=42,
                          lowest="C2", highest="A5")
    assert (spec.lowest, spec.highest) == ("C2", "A5")
    assert spec.midi_program == 42
