"""Tests for the YAML command registry."""

from __future__ import annotations

import pytest

from dorico_maestro.models import CmdStatus
from dorico_maestro.registry import Registry, default_registry

# Commands the interface contract guarantees are marked verified.
KNOWN_VERIFIED = [
    "Edit.SelectAll",
    "Edit.SelectNone",
    "Edit.Copy",
    "NoteInput.Enter",
    "NoteInput.Exit",
    "NoteInput.NoteValue?LogDuration=kQuaver",
    "NoteInput.Pitch",
    "Window.SwitchMode",
    "Play.Stop",
]

# Namespaces from docs/protocol.md that must be present in the catalog.
EXPECTED_CATEGORIES = [
    "NoteInput",
    "EventEdit",
    "Play",
    "Window",
    "Edit",
    "View",
    "Setup",
    "File",
    "Project",
    "NoteEdit",
]


def test_default_registry_is_cached_singleton() -> None:
    assert default_registry() is default_registry()


def test_registry_loads_full_catalog() -> None:
    reg = Registry.load()
    # The Dorico 6 catalog is ~340 commands (plus a few canonical base forms).
    assert 330 <= len(reg.all()) <= 360


def test_expected_categories_present() -> None:
    reg = Registry.load()
    categories = {s.category for s in reg.all()}
    for cat in EXPECTED_CATEGORIES:
        assert cat in categories, f"missing category {cat}"


def test_known_verified_are_marked_verified() -> None:
    reg = Registry.load()
    for cmd_id in KNOWN_VERIFIED:
        spec = reg.get(cmd_id)
        assert spec.status is CmdStatus.VERIFIED, f"{cmd_id} should be verified"


def test_get_unknown_raises_keyerror() -> None:
    reg = Registry.load()
    with pytest.raises(KeyError):
        reg.get("Totally.Unknown")


def test_by_category_matches_catalog_order() -> None:
    reg = Registry.load()
    note_input = reg.by_category("NoteInput")
    assert note_input, "NoteInput category must not be empty"
    assert all(s.category == "NoteInput" for s in note_input)
    # by_category is a subset of all(), preserving order.
    all_ids = [s.id for s in reg.all()]
    subset_ids = [s.id for s in note_input]
    assert subset_ids == [i for i in all_ids if i in set(subset_ids)]


def test_status_counts_sum_to_total() -> None:
    reg = Registry.load()
    counts = reg.status_counts()
    # All three statuses always present, even when zero.
    assert set(counts) == {s.value for s in CmdStatus}
    assert sum(counts.values()) == len(reg.all())
    assert counts["verified"] >= len(KNOWN_VERIFIED)


def test_pitch_spec_has_expected_params() -> None:
    reg = Registry.load()
    spec = reg.get("NoteInput.Pitch")
    assert spec.requires_note_input is True
    by_name = {p.name: p for p in spec.params}
    assert by_name["pitch"].dorico == "Pitch"
    assert by_name["pitch"].required is True
    assert by_name["octave"].dorico == "OctaveValue"
    assert by_name["octave"].kind == "int"
    # Accidentals are handled by a pre-step (add_notes), not a Pitch parameter.
    assert "accidental" not in by_name


def test_no_duplicate_ids() -> None:
    reg = Registry.load()
    ids = [s.id for s in reg.all()]
    dups = sorted(i for i in set(ids) if ids.count(i) > 1)
    assert not dups, f"duplicate command ids in catalog: {dups}"
