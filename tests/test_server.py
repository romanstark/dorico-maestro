"""Tests for the MCP surface (server.py) with a fake client."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from dorico_maestro import server
from dorico_maestro.models import Response


class FakeClient:
    """Stand-in for DoricoClient: records commands, never opens a socket."""

    token_path = Path("session_token.json")

    def __init__(self, *, ok: bool = True, status: dict[str, Any] | None = None) -> None:
        self.sent: list[str] = []
        self._ok = ok
        self._status = status or {}
        self.connected = True
        self.notifications: dict[str, int] = {}
        self.offered: dict[str, dict[str, list[str]]] = {}
        self.replies: dict[str, dict[str, Any]] = {}

    async def connect(self) -> bool:
        return True

    async def send(self, command: str, timeout: float | None = None) -> Response:
        self.sent.append(command)
        if self._ok:
            return Response(ok=True, code="kOK")
        return Response(ok=False, code="kError", detail="kUnknownCommand")

    async def status(self, wait: float = 2.0) -> dict[str, Any]:
        return dict(self._status)

    async def fresh_status(self, wait: float = 2.0) -> dict[str, Any]:
        return dict(self._status)

    async def edition(self) -> dict[str, Any]:
        return {"variant": "Elements", "number": "6.2.40"}

    async def commands(self) -> dict[str, dict[str, list[str]]]:
        return self.offered

    async def query(self, message: str, timeout: float = 5.0, **fields: Any) -> Response:
        data = self.replies.get(message)
        if data is None:
            return Response(ok=False, code="kError", detail="kNoScore")
        return Response(ok=True, code="kOK", data=data)


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
    # The leading Exit is the toggle guard: NoteInput.Enter switches note input
    # OFF when it is already on, so the session normalises the state first.
    assert fake.sent == [
        "NoteInput.Exit",
        "NoteInput.Enter",
        "NoteInput.NoteValue?LogDuration=kCrotchet",
        "NoteInput.SetAccidental?Type=kNatural",
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
    assert set(payload["status_counts"]) == {
        "verified",
        "reachable",
        "unavailable",
        "broken",
        "untested",
    }


def test_commands_filtered_by_status() -> None:
    payload = json.loads(server.commands_filtered("unavailable"))
    assert payload["filter"] == {"status": "unavailable"}
    assert payload["count"] == len(payload["commands"])
    assert payload["count"] >= 1
    assert {c["status"] for c in payload["commands"]} == {"unavailable"}


def test_a_status_with_no_rows_still_answers_cleanly() -> None:
    """Return an empty response when filtering by a status with zero rows."""
    payload = json.loads(server.commands_filtered("broken"))
    assert payload["filter"] == {"status": "broken"}
    assert payload["count"] == 0
    assert payload["commands"] == []


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
    # The leading Exit is the toggle guard: NoteInput.Enter switches note input
    # OFF when it is already on, so the session normalises the state first.
    assert fake.sent == [
        "NoteInput.Exit",
        "NoteInput.Enter",
        "NoteInput.NoteValue?LogDuration=kMinim",
        "NoteInput.RestMode",
        "NoteInput.Pitch?Pitch=X",
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
async def test_save_waits_for_the_file_and_tests_the_zip(
    fake: FakeClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "piece.dorico"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("score.dtn", b"x")
    monkeypatch.setattr(server, "LogWatch", fake_watch([f"Saving document: 1 to {project}"]))

    result = await server.save()

    assert result["success"] is True, result
    assert fake.sent == ["File.Save"]
    assert result["file"]["complete"] is True
    assert result["file"]["path"] == str(project)


def fake_watch(lines: list[str], finished: bool = True) -> type:
    """Return a LogWatch stand-in that has already seen ``lines``."""

    class Watch:
        available = True

        def lines(self) -> list[str]:
            return list(lines)

        async def finished(self, command: str, timeout: float) -> str | None:
            return f"notifyPostCommandExecute: {command} (5 ms)" if finished else None

        async def wait_for(self, needle: str, timeout: float) -> str | None:
            return needle

    return Watch


# ------------------------------------------------------- what a write displaces
async def test_a_write_reports_the_mode_it_landed_in(monkeypatch: Any) -> None:
    """Report whether active note-input mode will overwrite existing notes.

    read_selection returns neither pitch nor bar position. In kOverwrite mode,
    writing notes replaces existing content in those bars. The tool warns callers
    by inspecting noteInputMode in the application status.
    """
    client = FakeClient(status={"noteInputMode": "kOverwrite", "canUndo": True})
    monkeypatch.setattr(server, "_client_instance", lambda: client)
    result = await server.add_notes(["C4"], duration="quarter")
    assert result["success"] is True
    assert result["note_input_mode"] == "kOverwrite"
    assert result["displaces_existing"] is True
    assert "OVERWRITE" in result["mode_note"]


async def test_insert_mode_says_so_rather_than_staying_silent(monkeypatch: Any) -> None:
    """Verify that kInsert mode explicitly confirms non-destructive input."""
    client = FakeClient(status={"noteInputMode": "kInsert", "canUndo": True})
    monkeypatch.setattr(server, "_client_instance", lambda: client)
    result = await server.add_notes(["C4"], duration="quarter")
    assert result["displaces_existing"] is False
    assert result["note_input_mode"] == "kInsert"
    assert "kInsert" in result["mode_note"]


async def test_a_rest_reports_the_mode_too(monkeypatch: Any) -> None:
    """Verify that add_rest returns the active note input mode."""
    client = FakeClient(status={"noteInputMode": "kOverwrite"})
    monkeypatch.setattr(server, "_client_instance", lambda: client)
    result = await server.add_rest(duration="quarter")
    assert result["displaces_existing"] is True


# ------------------------------------------------------------------ export_pdf
async def test_export_pdf_sends_the_current_layout_command(fake: FakeClient) -> None:
    result = await server.export_pdf()
    assert result["success"] is True
    assert fake.sent == ["Print.ExportCurrentLayoutAsPDF"]


async def test_export_pdf_all_layouts_picks_the_sibling_command(fake: FakeClient) -> None:
    await server.export_pdf(all_layouts=True)
    assert fake.sent == ["Print.ExportAllLayoutsAsPDF"]


def test_export_pdf_warns_that_kok_is_not_proof_of_a_file() -> None:
    """Verify that export_pdf documents modal dialog queuing caveats.

    Dorico executes commands on its UI thread. When a modal dialog is open,
    remote export requests return kOK immediately while awaiting dialog dismissal.
    The tool docstring must state that kOK does not guarantee file creation.
    """
    doc = server.export_pdf.__doc__ or ""
    assert "does not prove a file exists" in doc
    assert "modal dialog" in doc
