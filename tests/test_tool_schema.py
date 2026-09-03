"""Validate MCP tool JSON schemas against expected structural specifications.

Clients construct arguments from published JSON schemas. Complex input structures
such as ScoreSpec must be exposed with explicit field names and types rather than
generic object dictionaries.

These tests run at import time without requiring a running Dorico instance.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dorico_maestro import server
from dorico_maestro.music.score import ScoreSpecError, score_from_dict
from dorico_maestro.toolargs import EventIn, PartIn, ScoreIn, score_dict

#: The one argument allowed to carry no shape, with the reason attached.
#: ``run_command`` forwards parameters to any of 348 catalogued commands, whose
#: parameter names and types differ per command and are declared in the catalog,
#: not here. A shape at this seam would be a false narrowing; ``search_commands``
#: is how a caller learns what a given command takes.
TYPELESS_BY_DESIGN = {("run_command", "params")}


def _tools() -> list[Any]:
    """Every registered tool with its client-visible input schema."""
    return asyncio.run(server.mcp.list_tools())


def _resolve(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Follow a local ``$ref`` one hop. Anything else comes back unchanged."""
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        return schema
    return defs.get(ref.split("/")[-1], {})


def _says_nothing_about_its_contents(schema: dict[str, Any], defs: dict[str, Any]) -> str | None:
    """Name the defect if this argument declares a container without a shape."""
    for branch in schema.get("anyOf") or [schema]:
        branch = _resolve(branch, defs)
        if branch.get("type") == "object":
            typed_values = isinstance(branch.get("additionalProperties"), dict)
            if not branch.get("properties") and not typed_values:
                return "an object with neither declared properties nor a typed value schema"
        if branch.get("type") == "array":
            items = _resolve(branch.get("items") or {}, defs)
            if not items:
                return "an array whose items declare no type at all"
            if items.get("type") == "object" and not items.get("properties"):
                return "an array of objects with no declared properties"
    return None


def test_no_tool_argument_declares_a_container_without_a_shape() -> None:
    """Every nested argument names its fields and their types, or is exempt on record."""
    offenders: list[str] = []
    for tool in _tools():
        schema = tool.inputSchema
        defs = schema.get("$defs") or {}
        for name, prop in (schema.get("properties") or {}).items():
            if (tool.name, name) in TYPELESS_BY_DESIGN:
                continue
            defect = _says_nothing_about_its_contents(prop, defs)
            if defect is not None:
                offenders.append(f"{tool.name}.{name} is {defect}")
    assert not offenders, (
        "these arguments tell a client nothing about their contents:\n  "
        + "\n  ".join(offenders)
        + "\nDeclare a model in dorico_maestro.toolargs, or add the pair to "
        "TYPELESS_BY_DESIGN with the reason it cannot be typed."
    )


def test_the_exemption_list_stays_a_list_of_real_arguments() -> None:
    """An exemption for an argument that no longer exists hides a real gap."""
    known = {
        (tool.name, name)
        for tool in _tools()
        for name in (tool.inputSchema.get("properties") or {})
    }
    stale = sorted(TYPELESS_BY_DESIGN - known)
    assert not stale, f"TYPELESS_BY_DESIGN names arguments that are gone: {stale}"


def test_the_score_shape_reaches_the_client_through_every_tool_that_takes_one() -> None:
    """Five tools take a score, and the shape has to travel with each of them.

    ``$defs`` and ``$ref`` are only useful if both halves arrive together, so this
    resolves the reference the way a client has to.
    """
    for name in (
        "write_score",
        "render_to_dorico",
        "export_musicxml",
        "analyze_harmony",
        "check_voice_leading",
    ):
        tool = next(t for t in _tools() if t.name == name)
        schema = tool.inputSchema
        defs = schema.get("$defs") or {}
        score = _resolve(schema["properties"]["score"], defs)
        assert score.get("properties"), f"{name}: the ScoreIn definition did not travel"
        part = _resolve((score["properties"]["parts"].get("anyOf") or [{}])[0], defs)
        item = _resolve(part.get("items") or {}, defs)
        assert item.get("required") == ["name"], f"{name}: a part's required name is unstated"


def test_the_allowed_enum_values_are_visible_in_the_schema() -> None:
    """A client that can see the vocabulary does not have to guess at it.

    The enum-valued fields are typed str on purpose: score_from_dict
    owns validation and its error message identifies valid values. The
    vocabulary is documented in field descriptions.
    """
    tool = next(t for t in _tools() if t.name == "write_score")
    defs = tool.inputSchema["$defs"]
    assert "quarter" in defs["EventIn"]["properties"]["duration"]["description"]
    assert "treble" in defs["StaffIn"]["properties"]["clef"]["description"]


def test_the_models_declare_every_key_the_loader_accepts() -> None:
    """Two lists of allowed keys that can drift apart are one list too many."""
    from dorico_maestro.music.score import _EVENT_KEYS, _PART_KEYS

    assert set(EventIn.model_fields) == set(_EVENT_KEYS), (
        "EventIn and _EVENT_KEYS disagree about what an event carries; "
        f"only in EventIn: {sorted(set(EventIn.model_fields) - set(_EVENT_KEYS))}, "
        f"only in the loader: {sorted(set(_EVENT_KEYS) - set(EventIn.model_fields))}"
    )
    assert set(PartIn.model_fields) == set(_PART_KEYS), (
        "PartIn and _PART_KEYS disagree about what a part carries; "
        f"only in PartIn: {sorted(set(PartIn.model_fields) - set(_PART_KEYS))}, "
        f"only in the loader: {sorted(set(_PART_KEYS) - set(PartIn.model_fields))}"
    )


def test_an_omitted_field_stays_omitted_instead_of_becoming_null() -> None:
    """This is the property that makes the models safe to add at all.

    Every field on these models is optional with a None default. A dump that
    kept unset fields would hand score_from_dict an event asking for
    duration: null (which is rejected) where the same event used to take
    the default. exclude_unset ensures unset fields are not emitted.
    """
    model = ScoreIn.model_validate(
        {"parts": [{"name": "Sopran", "events": [{"pitches": ["C4"]}]}]}
    )
    carried = score_dict(model)
    assert carried == {"parts": [{"name": "Sopran", "events": [{"pitches": ["C4"]}]}]}

    spec = score_from_dict(carried)
    event = spec.parts[0].staves[0].voices[0].events[0]
    assert event.duration.value == "quarter", "the default duration was lost"
    assert event.dots == 0


def test_a_stringified_number_is_coerced_rather_than_refused() -> None:
    """Coerce string-encoded numeric and boolean inputs to their native types."""
    event = EventIn.model_validate({"pitches": ["C4"], "dots": "1", "tie": "true"})
    carried = score_dict(event)
    assert carried == {"pitches": ["C4"], "dots": 1, "tie": True}


def test_plain_dicts_survive_the_seam_untouched() -> None:
    """Ensure plain dictionaries pass through tool input transformation untouched."""
    plain = {"parts": [{"name": "Sopran", "events": [{"pitches": ["C4"]}]}]}
    assert score_dict(plain) == plain
    assert score_dict("not a score") == "not a score", (
        "a non-mapping must reach score_from_dict so it can be named with its path"
    )


def test_an_unknown_key_still_reaches_the_loader_with_its_own_message() -> None:
    """Preserve loader hints for common parameter naming mistakes."""
    model = PartIn.model_validate({"name": "Sopran", "notes": [{"pitches": ["C4"]}]})
    carried = score_dict(model)
    assert carried["notes"] == [{"pitches": ["C4"]}]

    with pytest.raises(ScoreSpecError) as caught:
        score_from_dict({"parts": [carried]})
    assert "events" in str(caught.value), "the loader's hint was lost"
