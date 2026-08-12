"""Unit tests for the spec builder/validator — pure data, no Dorico."""

from __future__ import annotations

import pytest

from dorico_maestro.models import CmdStatus
from dorico_maestro.spec import CommandSpec, ParamSpec, build, validate


def _pitch_spec() -> CommandSpec:
    return CommandSpec(
        id="NoteInput.Pitch",
        category="NoteInput",
        params=[
            ParamSpec(name="pitch", dorico="Pitch", kind="enum",
                      enum=["A", "B", "C", "D", "E", "F", "G"], required=True),
            ParamSpec(name="octave", dorico="OctaveValue", kind="int", required=True),
            ParamSpec(name="accidental", dorico=None, kind="enum",
                      enum=["Sharp", "Flat", "Natural"], required=False),
        ],
    )


def test_defaults() -> None:
    spec = CommandSpec(id="Edit.SelectAll", category="Edit")
    assert spec.params == []
    assert spec.status is CmdStatus.UNTESTED
    assert spec.requires_note_input is False
    assert spec.destructive is False


def test_build_bare_id_no_params() -> None:
    spec = CommandSpec(id="Edit.SelectAll", category="Edit")
    assert build(spec) == "Edit.SelectAll"


def test_build_bare_id_when_no_args_supplied() -> None:
    # A spec with optional params but nothing supplied -> no query string.
    spec = CommandSpec(
        id="File.Save",
        category="File",
        params=[ParamSpec(name="path", dorico="File")],
    )
    assert build(spec) == "File.Save"


def test_build_assembles_query() -> None:
    spec = _pitch_spec()
    assert build(spec, pitch="C", octave=4) == "NoteInput.Pitch?Pitch=C&OctaveValue=4"


def test_supplying_none_dorico_param_raises() -> None:
    # 'accidental' has dorico=None -> supplying it to the generic path is a loud
    # error, not a silent drop (it must go through a higher layer, e.g. add_notes).
    spec = _pitch_spec()
    with pytest.raises(ValueError, match="handled by a higher layer"):
        build(spec, pitch="F", octave=5, accidental="Sharp")


def test_build_skips_unsupplied_optional() -> None:
    spec = CommandSpec(
        id="Play.StartOrStop",
        category="Play",
        params=[ParamSpec(name="location", dorico="PlayFromLocation")],
    )
    assert build(spec) == "Play.StartOrStop"
    assert build(spec, location="kPlayhead") == "Play.StartOrStop?PlayFromLocation=kPlayhead"


def test_build_url_encodes_values() -> None:
    spec = CommandSpec(
        id="File.Open",
        category="File",
        params=[ParamSpec(name="file", dorico="File", required=True)],
    )
    out = build(spec, file="C:/My Scores/piece & sketch.dorico")
    # Space, slash, ampersand and colon all percent-encoded (safe='').
    assert out == "File.Open?File=C%3A%2FMy%20Scores%2Fpiece%20%26%20sketch.dorico"


def test_validate_missing_required() -> None:
    spec = _pitch_spec()
    with pytest.raises(ValueError, match="missing required parameter 'pitch'"):
        validate(spec, octave=4)


def test_validate_bad_enum() -> None:
    spec = _pitch_spec()
    with pytest.raises(ValueError, match="not a valid choice"):
        validate(spec, pitch="H", octave=4)


def test_validate_non_int() -> None:
    spec = _pitch_spec()
    with pytest.raises(ValueError, match="not an integer"):
        validate(spec, pitch="C", octave="high")


def test_validate_accepts_int_castable_string() -> None:
    spec = _pitch_spec()
    # "4" is int-castable -> no error.
    validate(spec, pitch="C", octave="4")


def test_validate_rejects_unknown_args() -> None:
    spec = _pitch_spec()
    # Unknown args are rejected so a mistyped/unsupported value can't be silently dropped.
    with pytest.raises(ValueError, match="unknown argument"):
        validate(spec, pitch="C", octave=4, nonsense=True)


def test_build_validates_first() -> None:
    spec = _pitch_spec()
    with pytest.raises(ValueError):
        build(spec, octave=4)  # missing required 'pitch'
