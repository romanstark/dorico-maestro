"""Tests for the music/composition MCP tools (server.py) with a fake client.

No real Dorico, no socket: the recording ``FakeClient`` from the test suite is
monkeypatched in as the shared client singleton. Offline theory tools must send
nothing; the caret path must send exactly ``render.plan_flow(spec)[0]``.
"""

from __future__ import annotations

from typing import Any

import pytest

from dorico_maestro import render, server
from dorico_maestro.models import Response


class FakeClient:
    """Stand-in for DoricoClient: records commands, never opens a socket."""

    def __init__(self, *, ok: bool = True, status: dict[str, Any] | None = None) -> None:
        self.sent: list[str] = []
        self._ok = ok
        self._status = status or {}
        self.connected = True

    async def connect(self) -> bool:
        return True

    async def send(self, command: str, timeout: float | None = None) -> Response:
        self.sent.append(command)
        if self._ok:
            return Response(ok=True, code="kOK")
        return Response(ok=False, code="kError", detail="kUnknownCommand")

    async def status(self, wait: float = 2.0) -> dict[str, Any]:
        return dict(self._status)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setattr(server, "_client", None)
    monkeypatch.setattr(server, "_client_instance", lambda: client)
    return client


# The golden two-bar, two-staff piano fixture (contract §2.3).
WORKED_EXAMPLE: dict[str, Any] = {
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

# The exact caret sequence the caret path must emit (contract §3.5).
GOLDEN_COMMANDS: list[str] = [
    "NoteInput.Enter",
    "NoteInput.MoveUpTop",
    "NoteInput.MoveLeftBar",
    "NoteInput.MoveLeftBar",
    "NoteInput.NoteValue?LogDuration=kCrotchet",
    "NoteInput.Pitch?Pitch=E&OctaveValue=4",
    "NoteInput.Pitch?Pitch=G&OctaveValue=4",
    "NoteInput.SetArticulation?Value=kStaccato",
    "NoteInput.Pitch?Pitch=C&OctaveValue=5",
    "NoteInput.SetArticulation?Value=kStaccato",
    "NoteInput.Pitch?Pitch=B&OctaveValue=4",
    "NoteInput.NoteValue?LogDuration=kMinim",
    "NoteInput.StartEndChord",
    "NoteInput.Pitch?Pitch=C&OctaveValue=5",
    "NoteInput.Pitch?Pitch=E&OctaveValue=5",
    "NoteInput.Pitch?Pitch=G&OctaveValue=5",
    "NoteInput.StartEndChord",
    "NoteInput.RestMode",
    "NoteInput.MoveUpTop",
    "NoteInput.MoveLeftBar",
    "NoteInput.MoveLeftBar",
    "NoteInput.MoveDown",
    "NoteInput.NoteValue?LogDuration=kMinim",
    "NoteInput.Pitch?Pitch=C&OctaveValue=3",
    "NoteInput.Pitch?Pitch=G&OctaveValue=2",
    "NoteInput.NoteValue?LogDuration=kSemibreve",
    "NoteInput.Pitch?Pitch=C&OctaveValue=2",
    "NoteInput.Exit",
]


# ------------------------------------------------------------------- write_score
async def test_write_score_caret_sends_golden(fake: FakeClient) -> None:
    result = await server.write_score(WORKED_EXAMPLE)
    assert result["success"] is True
    assert result["method"] == "caret"
    assert fake.sent == GOLDEN_COMMANDS


async def test_write_score_reports_analysis_and_dynamics_warning(fake: FakeClient) -> None:
    result = await server.write_score(WORKED_EXAMPLE)
    assert "analysis" in result
    assert "ranges" in result["analysis"]
    assert "voice_leading" in result["analysis"]
    assert any("dynamic 'mp' dropped" in w for w in result["warnings"])
    assert result["caveat"]


async def test_write_score_invalid_sends_nothing(fake: FakeClient) -> None:
    result = await server.write_score({"parts": []})
    assert result["success"] is False
    assert "error" in result
    assert fake.sent == []


async def test_write_score_unknown_method_sends_nothing(fake: FakeClient) -> None:
    result = await server.write_score(WORKED_EXAMPLE, method="telepathy")
    assert result["success"] is False
    assert fake.sent == []


async def test_write_score_musicxml_exports_file_and_imports_it(
    fake: FakeClient, tmp_path: Any
) -> None:
    # The musicxml method exports a real, parseable file AND fires the Remote-API
    # import — it hands back both the file path and the import result.
    from dorico_maestro.music.musicxml import parse_musicxml

    result = await server.write_score(WORKED_EXAMPLE, method="musicxml")
    assert result["success"] is True
    assert result["method"] == "musicxml"
    summary = parse_musicxml(result["exported"])
    assert summary["note_count"] > 0
    # The exported file is imported over the API (one parameterised File.Open).
    assert any(c.startswith("File.Open?File=") for c in fake.sent)
    assert result["imported"]["requires_confirmation"] is True
    assert "new flow" in result["warnings"][0].lower()


# --------------------------------------------------------------- render_to_dorico
async def test_render_to_dorico_dry_run_sends_nothing(fake: FakeClient) -> None:
    result = await server.render_to_dorico(WORKED_EXAMPLE, dry_run=True)
    assert result["success"] is True
    assert result["commands"] == GOLDEN_COMMANDS
    assert fake.sent == []


async def test_render_to_dorico_live_sends_golden(fake: FakeClient) -> None:
    result = await server.render_to_dorico(WORKED_EXAMPLE)
    assert result["success"] is True
    assert fake.sent == GOLDEN_COMMANDS


# ------------------------------------------------------------------ export/import
def test_export_musicxml_writes_parseable_file(fake: FakeClient, tmp_path: Any) -> None:
    out = tmp_path / "sketch.musicxml"
    result = server.export_musicxml(WORKED_EXAMPLE, str(out))
    assert result["success"] is True
    from dorico_maestro.music.musicxml import parse_musicxml

    summary = parse_musicxml(result["path"])
    assert summary["part_count"] >= 1
    assert summary["note_count"] > 0
    assert fake.sent == []


def test_export_musicxml_invalid_spec(fake: FakeClient) -> None:
    result = server.export_musicxml({"parts": []})
    assert result["success"] is False
    assert fake.sent == []


def test_export_musicxml_default_temp_path(fake: FakeClient) -> None:
    # With no explicit path the tool writes to a temp file; pin that the returned
    # path exists and is parseable, then clean it up (the tool does not).
    import os

    from dorico_maestro.music.musicxml import parse_musicxml

    result = server.export_musicxml(WORKED_EXAMPLE)
    assert result["success"] is True
    path = result["path"]
    try:
        assert os.path.exists(path)
        summary = parse_musicxml(path)
        assert summary["note_count"] > 0
        assert fake.sent == []
    finally:
        os.unlink(path)


async def test_import_musicxml_launches_and_flags_confirmation(fake: FakeClient) -> None:
    # The tool launches the import and flags that Dorico may prompt for a new
    # player the user must confirm.
    result = await server.import_musicxml("C:/nonexistent/whatever.musicxml")
    assert result["success"] is True
    assert result["supported"] is True
    assert result["requires_confirmation"] is True
    assert any(c.startswith("File.Open?File=") for c in fake.sent)


def test_read_score_tool_reads_content(fake: FakeClient, tmp_path: Any) -> None:
    # Reads an exported MusicXML file back bar by bar; no Dorico contact.
    from dorico_maestro.music.musicxml import score_to_musicxml
    from dorico_maestro.music.score import score_from_dict

    written = score_to_musicxml(score_from_dict(WORKED_EXAMPLE), tmp_path / "worked.musicxml")
    result = server.read_score(written)
    assert result["success"] is True
    assert result["part_count"] >= 1
    assert result["parts"][0]["measures"]
    assert fake.sent == []


def test_read_score_tool_missing_file(fake: FakeClient) -> None:
    result = server.read_score("C:/nope/none.musicxml")
    assert result["success"] is False
    assert "not found" in result["error"].lower()


def test_score_schema_tool_returns_shape() -> None:
    result = server.score_schema()
    assert result["success"] is True
    assert "minimal_flat" in result and "enums" in result
    assert "quarter" in result["enums"]["duration"]


async def test_write_score_zero_notes_is_loud(fake: FakeClient) -> None:
    # A part with no events parses but writes nothing — fail loudly, never "succeed".
    result = await server.write_score({"parts": [{"name": "Piano"}]})
    assert result["success"] is False
    assert "0 notes" in result["error"]
    assert "example" in result
    assert fake.sent == []


async def test_goto_bar_positions_and_reports(fake: FakeClient) -> None:
    result = await server.goto_bar(3, staff=1)
    assert result["success"] is True
    assert result["caret"] == {"bar": 3, "staff": 1, "beat": 1.0}
    # Deterministic anchor: Enter, up-top, rewind to bar 1, then advance.
    assert fake.sent[0] == "NoteInput.Enter"
    assert fake.sent[1] == "NoteInput.MoveUpTop"
    assert fake.sent.count("NoteInput.MoveLeftBar") == 64
    assert fake.sent.count("NoteInput.MoveRightBar") == 2  # bar 3 -> 2 right moves
    assert fake.sent.count("NoteInput.MoveDown") == 1  # staff 1 -> 1 down


async def test_goto_bar_rejects_bad_bar(fake: FakeClient) -> None:
    result = await server.goto_bar(0)
    assert result["success"] is False
    assert fake.sent == []  # nothing sent for an invalid bar


async def test_goto_bar_beat_steps_over_grid(fake: FakeClient) -> None:
    fake._status = {"rhythmicGridResolutionValue": "kCrotchet"}  # quarter grid
    result = await server.goto_bar(2, staff=0, beat=3)
    assert result["success"] is True
    assert result["caret"]["beat"] == 3.0
    assert fake.sent.count("NoteInput.MoveRight") == 2  # two quarter steps to beat 3


async def test_goto_bar_skips_enter_when_note_input_active(fake: FakeClient) -> None:
    # A second NoteInput.Enter would toggle note input OFF and break the moves, so
    # goto_bar must NOT re-enter when it is already active.
    fake._status = {"noteInputActive": True}
    result = await server.goto_bar(2, staff=0)
    assert result["success"] is True
    assert "NoteInput.Enter" not in fake.sent
    assert fake.sent[0] == "NoteInput.MoveUpTop"


async def test_read_selection_reports_properties(fake: FakeClient) -> None:
    fake._status = {
        "hasSelection": True,
        "selectedEventType": "kNoteEvent",
        "duration": "kQuaver",
        "rhythmDots": "0",
        "articulationStaccato": True,
        "accidental": "",
    }
    result = await server.read_selection()
    assert result["success"] is True
    assert result["has_selection"] is True
    assert result["event_type"] == "kNoteEvent"
    assert result["duration"] == "eighth"
    assert result["articulations"] == ["staccato"]
    assert result["accidental"] is None


# --------------------------------------------------------------- offline theory
def test_analyze_harmony_offline(fake: FakeClient) -> None:
    result = server.analyze_harmony(WORKED_EXAMPLE)
    assert result["success"] is True
    assert "key" in result
    assert isinstance(result["roman"], list)
    assert fake.sent == []


def test_check_voice_leading_offline(fake: FakeClient) -> None:
    result = server.check_voice_leading(WORKED_EXAMPLE)
    assert result["success"] is True
    assert isinstance(result["issues"], list)
    assert result["ok"] is (not result["issues"])
    assert fake.sent == []


def test_check_voice_leading_flags_parallel_fifths(fake: FakeClient) -> None:
    # Two one-voice staves a fifth apart, both rising: a textbook parallel fifth.
    # Asserts the theory->tool wiring surfaces concrete content, not just a list.
    spec = {
        "parts": [
            {
                "name": "Duet",
                "staves": [
                    {"voices": [{"index": 1, "events": [
                        {"pitch": "G4"}, {"pitch": "A4"},
                    ]}]},
                    {"voices": [{"index": 1, "events": [
                        {"pitch": "C4"}, {"pitch": "D4"},
                    ]}]},
                ],
            }
        ]
    }
    result = server.check_voice_leading(spec)
    assert result["success"] is True
    assert result["ok"] is False
    rules = {issue["rule"] for issue in result["issues"]}
    assert "parallel-fifth" in rules
    assert fake.sent == []


def test_analyze_harmony_tolerates_unparseable_key(fake: FakeClient) -> None:
    # A German-spelled key ("H minor" = B minor) is not parseable by theory.parse_key;
    # the tool must still return an envelope, not raise (regression for the guard in
    # roman_numeral_analysis).
    spec = {
        "key": "H minor",
        "parts": [
            {"name": "P", "staves": [{"voices": [{"index": 1, "events": [
                {"pitches": ["B3", "D4", "F#4"]},
            ]}]}]}
        ],
    }
    result = server.analyze_harmony(spec)
    assert result["success"] is True
    assert isinstance(result["roman"], list)
    # Unparseable key -> verticals returned but roman labels stay None.
    assert all(item["roman"] is None for item in result["roman"])
    assert fake.sent == []


def test_suggest_next_chord_offline(fake: FakeClient) -> None:
    result = server.suggest_next_chord("C major", ["I", "vi", "ii"])
    assert result["success"] is True
    assert isinstance(result["suggestions"], list)
    assert fake.sent == []


def test_instrument_range_lowest_highest(fake: FakeClient) -> None:
    result = server.instrument_range("cello")
    assert result["success"] is True
    assert "lowest" in result and "highest" in result
    assert fake.sent == []


def test_instrument_range_pitch_check(fake: FakeClient) -> None:
    inside = server.instrument_range("cello", "C2")
    outside = server.instrument_range("piccolo", "C4")
    assert inside["in_range"] is True
    assert outside["in_range"] is False
    assert fake.sent == []


def test_instrument_range_unknown_instrument(fake: FakeClient) -> None:
    result = server.instrument_range("wobblephone")
    assert result["success"] is False
    assert fake.sent == []


def test_check_counterpoint_offline(fake: FakeClient) -> None:
    result = server.check_counterpoint(["C4", "D4", "E4"], ["E4", "F4", "G4"])
    assert result["success"] is True
    assert isinstance(result["issues"], list)
    assert fake.sent == []


def test_check_counterpoint_unsupported_species(fake: FakeClient) -> None:
    result = server.check_counterpoint(["C4", "D4"], ["E4", "F4"], species=2)
    assert result["success"] is False
    assert fake.sent == []


# ------------------------------------------------------------- catalog discovery
def test_search_commands_filters_by_category_and_status(fake: FakeClient) -> None:
    result = server.search_commands(query="NoteValue", category="NoteInput", status="verified")
    assert result["success"] is True
    assert result["total"] >= 1
    assert all(c["category"] == "NoteInput" for c in result["commands"])
    assert all(c["status"] == "verified" for c in result["commands"])
    assert any("NoteValue" in c["id"] for c in result["commands"])
    assert fake.sent == []  # pure catalog lookup, no Dorico


def test_search_commands_orders_verified_first_and_honours_limit(fake: FakeClient) -> None:
    result = server.search_commands(limit=5)
    assert result["success"] is True
    assert result["count"] <= 5
    assert result["total"] > 5  # the catalog is large; limit really truncated it
    assert "status_counts" in result and "categories" in result
    # Once a non-verified status appears in the slice, no verified may follow it.
    seen_non_verified = False
    for c in result["commands"]:
        if c["status"] != "verified":
            seen_non_verified = True
        elif seen_non_verified:
            raise AssertionError("a verified command ranked after a non-verified one")
    assert fake.sent == []


# -------------------------------------------------------------- guided popover
async def test_open_popover_at_selection_opens_dynamic(fake: FakeClient) -> None:
    result = await server.open_popover("dynamic")
    assert result["success"] is True
    assert result["command"] == "NoteInput.CreateDynamic"
    assert result["experimental"] is False
    assert fake.sent == ["NoteInput.CreateDynamic"]  # no navigation without a bar
    assert "type" in result["instruction"].lower()


async def test_open_popover_navigates_to_bar_and_staff(fake: FakeClient) -> None:
    result = await server.open_popover("tempo", bar=3, staff=1)
    assert result["success"] is True
    assert result["experimental"] is True
    assert fake.sent[0] == "NoteInput.Enter"
    assert fake.sent[-1] == "NoteInput.CreateTempo"
    assert fake.sent.count("NoteInput.MoveRightBar") == 2  # bar 3 -> two steps right
    assert fake.sent.count("NoteInput.MoveDown") == 1  # staff index 1


async def test_open_popover_unknown_kind_sends_nothing(fake: FakeClient) -> None:
    result = await server.open_popover("glissando")
    assert result["success"] is False
    assert fake.sent == []


def test_render_module_plan_flow_matches_golden() -> None:
    from dorico_maestro.music.score import score_from_dict

    spec = score_from_dict(WORKED_EXAMPLE)
    commands, _ = render.plan_flow(spec)
    assert commands == GOLDEN_COMMANDS
