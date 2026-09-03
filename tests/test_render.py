"""Tests for the live caret render planner and its async wrappers.

Everything here is offline: a recording ``FakeClient`` stands in for Dorico (no
socket, no real note input), exactly as ``tests/test_session.py`` does. The
golden ``plan_flow`` sequence is the exact command list the planner must produce.
"""

from __future__ import annotations

from typing import Any

import pytest

from dorico_maestro.models import Response
from dorico_maestro.music.score import ScoreSpec, score_from_dict
from dorico_maestro.render import (
    RenderReport,
    bar_count,
    import_musicxml,
    plan_flow,
    render_score,
)


class FakeClient:
    """Records commands sent; no socket (mirrors tests/test_session.py)."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, command: str, timeout: float | None = None) -> Response:
        self.sent.append(command)
        return Response(ok=True, code="kOK")


class RaisingClient:
    """Records commands, but raises when a chosen command is sent."""

    def __init__(self, fail_on: str) -> None:
        self.sent: list[str] = []
        self.fail_on = fail_on

    async def send(self, command: str, timeout: float | None = None) -> Response:
        if command == self.fail_on:
            raise RuntimeError("boom")
        self.sent.append(command)
        return Response(ok=True, code="kOK")


class DirtyCaretClient:
    """A client whose caret already has two articulations toggled on (a prior edit
    left them). status() reports them so render_score can clear them first."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, command: str, timeout: float | None = None) -> Response:
        self.sent.append(command)
        return Response(ok=True, code="kOK")

    async def status(self, wait: float = 2.0) -> dict[str, Any]:
        return {"articulationAccent": True, "articulationTenuto": True}


# The golden fixture: a ScoreSpec dict in the shape docs/architecture.md sets out
# under "The 'music brain'": parts -> staves -> voices -> events. Two bars of C
# major 4/4 for two-staff piano, sized to exercise every planner branch at once:
# a plain note, a dynamic (dropped with a warning), an articulation toggled on and
# off around its note, a chord, a rest, and a second staff the caret has to move
# down to. The key, tempo and clefs it carries are not enterable through the caret
# path, so the planner must drop them rather than emit a command.
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

# The byte-identical expected output of plan_flow(WORKED_EXAMPLE)[0]. Its shape is
# the one plan_flow's own docstring specifies: NoteInput.Enter, then for each staff
# in system order MoveUpTop -> MoveLeftBar x bar_count -> MoveDown x staff index
# followed by that staff's notes, and NoteInput.Exit last. Every command below
# resolves to a row marked verified in src/dorico_maestro/commands.yaml (the
# concrete ones directly, the NoteInput.Pitch?... ones through the parameterised
# NoteInput.Pitch spec).
GOLDEN: list[str] = [
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


def _worked_spec() -> ScoreSpec:
    return score_from_dict(WORKED_EXAMPLE)


# ---------------------------------------------------------------- plan_flow
def test_plan_flow_matches_golden_sequence() -> None:
    commands, _warnings = plan_flow(_worked_spec())
    assert commands == GOLDEN
    assert len(commands) == 28


def test_plan_flow_warns_dropped_dynamic() -> None:
    _commands, warnings = plan_flow(_worked_spec())
    assert (
        "Piano/staff 0 voice 1 event 0: dynamic 'mp' dropped: "
        "not enterable live; use export_musicxml"
    ) in warnings


def test_plan_flow_emits_rest_without_best_effort_warning() -> None:
    # RestMode is emitted and not flagged best-effort.
    commands, warnings = plan_flow(_worked_spec())
    assert "NoteInput.RestMode" in commands
    assert not any("RestMode" in w and "best-effort" in w for w in warnings)


def test_plan_flow_does_not_emit_clef_but_warns() -> None:
    commands, warnings = plan_flow(_worked_spec())
    assert not any("Clef" in c for c in commands)
    assert any("clef 'bass'" in w for w in warnings)


# -------------------------------------------------------- dots & ties (plan)
def test_plan_staff_emits_cyclenumdots_for_dotted_note() -> None:
    # A dotted quarter: the NoteValue is emitted, then exactly one CycleNumDots
    # per dot, before the pitch itself.
    spec = score_from_dict(
        {
            "parts": [
                {
                    "name": "Flute",
                    "staves": [
                        {
                            "voices": [
                                {
                                    "index": 1,
                                    "events": [
                                        {"pitch": "C5", "duration": "quarter", "dots": 1}
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    )
    commands, _warnings = plan_flow(spec)
    nv = commands.index("NoteInput.NoteValue?LogDuration=kCrotchet")
    assert commands[nv + 1] == "NoteInput.CycleNumDots"
    assert commands[nv + 2] == "NoteInput.Pitch?Pitch=C&OctaveValue=5"
    assert commands.count("NoteInput.CycleNumDots") == 1


def test_plan_staff_emits_two_cyclenumdots_for_double_dotted_note() -> None:
    spec = score_from_dict(
        {
            "parts": [
                {
                    "name": "Flute",
                    "staves": [
                        {
                            "voices": [
                                {
                                    "index": 1,
                                    "events": [
                                        {"pitch": "C5", "duration": "half", "dots": 2}
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    )
    commands, _warnings = plan_flow(spec)
    assert commands.count("NoteInput.CycleNumDots") == 2


def test_plan_staff_emits_tie_after_pitch() -> None:
    # A tied note emits NoteInput.Tie after the pitch.
    spec = score_from_dict(
        {
            "parts": [
                {
                    "name": "Flute",
                    "staves": [
                        {
                            "voices": [
                                {
                                    "index": 1,
                                    "events": [
                                        {"pitch": "C5", "duration": "quarter", "tie": True},
                                        {"pitch": "C5", "duration": "quarter"},
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    )
    commands, warnings = plan_flow(spec)
    tie = commands.index("NoteInput.Tie")
    # The Tie follows the first pitch (which precedes it in the command list).
    assert commands[tie - 1] == "NoteInput.Pitch?Pitch=C&OctaveValue=5"
    # A tie emits no best-effort warning.
    assert not any("best-effort" in w for w in warnings)


# ------------------------------------------------- articulations (persistent toggle)
def test_articulation_toggles_on_before_and_off_after_note() -> None:
    # SetArticulation is a persistent caret toggle, so an articulated note must
    # wrap its pitch: toggle ON before it, and OFF before the next (plain) note;
    # otherwise the articulation leaks onto every following note.
    spec = score_from_dict(
        {
            "parts": [
                {
                    "name": "P",
                    "staves": [
                        {"voices": [{"index": 1, "events": [
                            {"pitch": "C5", "articulations": ["accent"]},
                            {"pitch": "D5"},
                        ]}]}
                    ],
                }
            ]
        }
    )
    commands, _ = plan_flow(spec)
    acc = "NoteInput.SetArticulation?Value=kAccent"
    assert commands.count(acc) == 2  # on before C5, off before D5
    ci = commands.index("NoteInput.Pitch?Pitch=C&OctaveValue=5")
    di = commands.index("NoteInput.Pitch?Pitch=D&OctaveValue=5")
    assert commands[ci - 1] == acc  # toggled ON right before the accented note
    assert commands[di - 1] == acc  # toggled OFF right before the plain note


def test_same_articulation_is_not_retoggled_between_notes() -> None:
    # Two consecutive accented notes: accent goes ON once before the first and OFF
    # once after the last; it is not toggled in between.
    spec = score_from_dict(
        {
            "parts": [
                {
                    "name": "P",
                    "staves": [
                        {"voices": [{"index": 1, "events": [
                            {"pitch": "C5", "articulations": ["accent"]},
                            {"pitch": "D5", "articulations": ["accent"]},
                        ]}]}
                    ],
                }
            ]
        }
    )
    commands, _ = plan_flow(spec)
    assert commands.count("NoteInput.SetArticulation?Value=kAccent") == 2


# ----------------------------------------------------------------- bar_count
def test_bar_count_two_bars() -> None:
    spec = _worked_spec()
    assert bar_count(spec.parts[0], 4.0) == 2


def test_bar_count_rejects_nonpositive_bar_length() -> None:
    spec = _worked_spec()
    with pytest.raises(ValueError):
        bar_count(spec.parts[0], 0.0)


# --------------------------------------------------------------- render_score
async def test_render_score_sends_exactly_plan_flow() -> None:
    spec = _worked_spec()
    client = FakeClient()
    report = await render_score(client, spec)

    assert client.sent == GOLDEN
    assert isinstance(report, RenderReport)
    assert report.ok
    assert report.commands_planned == len(GOLDEN)
    assert report.commands_sent == report.commands_planned
    assert report.parts_rendered == 1
    assert report.experimental is False


async def test_render_score_dry_run_sends_nothing() -> None:
    spec = _worked_spec()
    client = FakeClient()
    report = await render_score(client, spec, dry_run=True)

    assert client.sent == []
    assert report.commands_sent == 0
    assert report.commands_planned > 0
    assert report.ok


async def test_render_score_clears_leftover_articulations_first() -> None:
    # A caret left dirty by a prior edit must be cleaned before rendering, so the
    # stuck articulations don't leak onto every note.
    spec = _worked_spec()
    client = DirtyCaretClient()
    await render_score(client, spec)

    kacc = "NoteInput.SetArticulation?Value=kAccent"
    kten = "NoteInput.SetArticulation?Value=kTenuto"
    assert kacc in client.sent and kten in client.sent  # both stuck ones toggled off
    first_pitch = next(
        i for i, c in enumerate(client.sent) if c.startswith("NoteInput.Pitch")
    )
    assert client.sent.index(kacc) < first_pitch  # cleared before any note is entered
    assert client.sent.index(kten) < first_pitch


async def test_render_score_closes_caret_on_error() -> None:
    spec = _worked_spec()
    # Fail on the second treble note; Exit must still be the last thing sent.
    client = RaisingClient(fail_on="NoteInput.Pitch?Pitch=G&OctaveValue=4")
    with pytest.raises(RuntimeError):
        await render_score(client, spec)
    assert client.sent[-1] == "NoteInput.Exit"
    assert "NoteInput.Enter" in client.sent


async def test_sharp_accidental_prestep_precedes_pitch() -> None:
    spec = score_from_dict(
        {
            "parts": [
                {
                    "name": "Flute",
                    "staves": [
                        {"voices": [{"index": 1, "events": [{"pitch": "F#5"}]}]}
                    ],
                }
            ]
        }
    )
    commands, _warnings = plan_flow(spec)
    acc = commands.index("NoteInput.SetAccidental?Type=kSharp")
    pitch = commands.index("NoteInput.Pitch?Pitch=F&OctaveValue=5")
    assert acc + 1 == pitch


async def test_multipart_spec_is_flagged_experimental() -> None:
    spec = score_from_dict(
        {
            "time": "4/4",
            "parts": [
                {
                    "name": "Flute",
                    "staves": [
                        {"voices": [{"index": 1, "events": [{"pitch": "C5", "duration": "whole"}]}]}
                    ],
                },
                {
                    "name": "Cello",
                    "staves": [
                        {"voices": [{"index": 1, "events": [{"pitch": "C3", "duration": "whole"}]}]}
                    ],
                },
            ],
        }
    )
    client = FakeClient()
    report = await render_score(client, spec)
    assert report.experimental is True
    assert client.sent == plan_flow(spec)[0]


# ------------------------------------------------------------- import_musicxml
async def test_import_musicxml_sends_raw_forward_slash_path(tmp_path: Any) -> None:
    xml = tmp_path / "score.xml"
    xml.write_text("<score/>", encoding="utf-8")
    client = FakeClient()

    result = await import_musicxml(client, xml)

    # kOK is accepted as "launched" (not proof the import completed).
    assert result["success"] is True
    assert result["supported"] is True
    assert result["attempted"] is True
    assert result["requires_confirmation"] is True
    assert result["code"] == "kOK"
    # Fires the parameterised open exactly once (avoids the bare picker dialog).
    assert len(client.sent) == 1
    sent = client.sent[0]
    assert sent.startswith("File.Open?File=")
    assert "FilterID=MusicXMLImportFilter" in sent
    # The path goes on the wire raw with forward slashes without percent-encoding
    # (%5C/%3A would stop Dorico finding the file).
    assert "%5C" not in sent and "%3A" not in sent
    assert "\\" not in sent
    # Warns about Dorico's own import behaviour (new flow / new-player dialog).
    assert "new flow" in result["note"].lower()


async def test_import_musicxml_returns_absolute_path(tmp_path: Any) -> None:
    sub = tmp_path / "nested"
    sub.mkdir()
    xml = sub / "s.xml"
    xml.write_text("<score/>", encoding="utf-8")
    client = FakeClient()

    result = await import_musicxml(client, xml)

    assert result["path"] == str(xml.resolve())
    assert result["success"] is True


async def test_import_musicxml_rejects_query_breaking_path(tmp_path: Any) -> None:
    # Dorico does not URL-decode the File value, so a path containing '&' would break
    # the command's mini-query (it would look like a second parameter). Reject up front
    # and send nothing rather than fire a mangled command.
    bad = tmp_path / "a & b.xml"
    bad.write_text("<score/>", encoding="utf-8")
    client = FakeClient()

    result = await import_musicxml(client, bad)

    assert result["success"] is False
    assert result["attempted"] is False
    assert client.sent == []


async def test_import_musicxml_accepts_spaces_in_path(tmp_path: Any) -> None:
    # A space is not one of the query-breaking chars (& ? #): Dorico's command parser
    # tolerates a raw space in the File= value, so a spaced path is sent as-is.
    xml = tmp_path / "my scores" / "a piece.musicxml"
    xml.parent.mkdir()
    xml.write_text("<score/>", encoding="utf-8")
    client = FakeClient()

    result = await import_musicxml(client, xml)

    assert result["success"] is True
    assert result["attempted"] is True
    assert len(client.sent) == 1
    assert " " in client.sent[0]  # the space goes on the wire raw
