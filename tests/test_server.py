"""Tests for the MCP surface (server.py) with a fake client — no real Dorico."""

from __future__ import annotations

import json
from typing import Any

import pytest

from dorico_maestro import server
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


# ------------------------------------------------------------------ window/transport
async def test_switch_mode_alias(fake: FakeClient) -> None:
    result = await server.switch_mode("write")
    assert fake.sent == ["Window.SwitchMode?WindowMode=kWriteMode"]
    assert result["success"] is True


async def test_switch_mode_unknown_sends_nothing(fake: FakeClient) -> None:
    result = await server.switch_mode("bogus")
    assert result["success"] is False
    assert fake.sent == []


async def test_playback_play_builds_location(fake: FakeClient) -> None:
    await server.playback("play")
    assert fake.sent == ["Play.StartOrStop?PlayFromLocation=kPlayhead"]


async def test_playback_stop(fake: FakeClient) -> None:
    await server.playback("stop")
    assert fake.sent == ["Play.Stop"]


async def test_playback_rewind_stops_then_returns_to_flow_start(fake: FakeClient) -> None:
    result = await server.playback("rewind")
    assert result["success"] is True
    assert fake.sent == ["Play.Stop", "Play.SetPlayheadToFlowStart"]


async def test_playback_bad_action_sends_nothing(fake: FakeClient) -> None:
    result = await server.playback("boogie")
    assert result["success"] is False
    assert fake.sent == []


# --------------------------------------------------------------------------- transpose
async def test_transpose_builds_correct_ids(fake: FakeClient) -> None:
    await server.transpose("up")
    await server.transpose("down", chromatic=True)
    await server.transpose("up", octave=True)
    assert fake.sent == [
        "NoteEdit.PitchUp",
        "NoteEdit.PitchDownChromatic",
        "NoteEdit.PitchUpOctave",
    ]


async def test_transpose_bad_direction(fake: FakeClient) -> None:
    result = await server.transpose("sideways")
    assert result["success"] is False
    assert fake.sent == []


# -------------------------------------------------------------------------- run_command
async def test_run_command_pops_confirm_and_runs_destructive(fake: FakeClient) -> None:
    result = await server.run_command("Edit.Delete", {"confirm": True})
    assert fake.sent == ["Edit.Delete"]
    assert result["success"] is True


async def test_run_command_destructive_blocked_without_confirm(fake: FakeClient) -> None:
    result = await server.run_command("Edit.Delete")
    assert result["success"] is False
    assert result.get("blocked") is True
    assert fake.sent == []


async def test_run_command_unknown_id(fake: FakeClient) -> None:
    result = await server.run_command("Totally.Unknown")
    assert result["success"] is False
    assert fake.sent == []


# ------------------------------------------------------------------------------- notes
async def test_add_notes_empty_input(fake: FakeClient) -> None:
    result = await server.add_notes([])
    assert result["success"] is False
    assert fake.sent == []


async def test_add_notes_bad_duration(fake: FakeClient) -> None:
    result = await server.add_notes(["C4"], duration="triangle")
    assert result["success"] is False
    assert fake.sent == []


async def test_add_notes_sequence(fake: FakeClient) -> None:
    result = await server.add_notes(["C4", "F#5"], duration="quarter")
    assert result["success"] is True
    assert fake.sent == [
        "NoteInput.Enter",
        "NoteInput.NoteValue?LogDuration=kCrotchet",
        "NoteInput.Pitch?Pitch=C&OctaveValue=4",
        "NoteInput.SetAccidental?Type=kSharp",
        "NoteInput.Pitch?Pitch=F&OctaveValue=5",
        "NoteInput.Exit",
    ]


# ------------------------------------------------------------ popover-limited (honest)
async def test_set_time_signature_is_honest_no_send(fake: FakeClient) -> None:
    result = await server.set_time_signature("3/4")
    assert result["success"] is False
    assert result["supported"] is False
    assert result["requested"] == "3/4"
    assert fake.sent == []  # value is NOT silently dropped onto the wire


async def test_navigate_bar_is_honest_no_send(fake: FakeClient) -> None:
    result = await server.navigate("bar", bar=12)
    assert result["success"] is False
    assert result["supported"] is False
    assert fake.sent == []


# ---------------------------------------------------------------------------- resource
def test_commands_resource_lists_catalog() -> None:
    payload = json.loads(server.commands_catalog())
    assert payload["count"] > 300
    assert "NoteInput" in payload["filters"]["categories"]
    assert set(payload["status_counts"]) == {"verified", "broken", "untested"}


def test_commands_filtered_by_status() -> None:
    payload = json.loads(server.commands_filtered("broken"))
    assert payload["filter"] == {"status": "broken"}
    assert payload["count"] == len(payload["commands"])
    assert payload["count"] >= 1


def test_commands_filtered_by_category() -> None:
    payload = json.loads(server.commands_filtered("NoteInput"))
    assert payload["filter"] == {"category": "NoteInput"}
    assert payload["count"] > 0


# ------------------------------------------------------------------ connection
async def test_connect_to_dorico_returns_status(fake: FakeClient) -> None:
    fake._status = {"windowMode": "kWriteMode"}
    result = await server.connect_to_dorico()
    assert result["success"] is True
    assert result["status"]["windowMode"] == "kWriteMode"


async def test_get_status_returns_snapshot(fake: FakeClient) -> None:
    fake._status = {"noteInputActive": False, "canUndo": True}
    result = await server.get_status()
    assert result["success"] is True
    assert result["status"]["canUndo"] is True


# ------------------------------------------------------------------- note entry
async def test_add_rest_sequence(fake: FakeClient) -> None:
    result = await server.add_rest("half")
    assert result["success"] is True
    assert result["duration"] == "half"
    assert fake.sent == [
        "NoteInput.Enter",
        "NoteInput.NoteValue?LogDuration=kMinim",
        "NoteInput.RestMode",
        "NoteInput.Exit",
    ]


async def test_add_rest_rejects_bad_duration(fake: FakeClient) -> None:
    result = await server.add_rest("bogus")
    assert result["success"] is False
    assert fake.sent == []  # a bad duration is never sent to Dorico


# ------------------------------------------------------------ popover-limited (honest)
async def test_set_key_signature_is_honest_no_send(fake: FakeClient) -> None:
    result = await server.set_key_signature("G major")
    assert result["success"] is False
    assert result["supported"] is False
    assert result["requested"] == "G major"
    assert fake.sent == []  # the tonality is never silently dropped onto the wire


# ------------------------------------------------------------------------- save
async def test_save_sends_file_save(fake: FakeClient) -> None:
    result = await server.save()
    assert result["success"] is True
    assert fake.sent == ["File.Save"]
