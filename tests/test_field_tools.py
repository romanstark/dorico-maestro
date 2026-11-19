"""Playback setup, flows, markings, project files and the requests behind them."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from dorico_maestro import applog, server
from dorico_maestro import playback as playsetup
from dorico_maestro.client import DoricoClient
from dorico_maestro.models import ConnectionState, Response


class FakeClient:
    """Stand-in for DoricoClient: records commands and answers read requests."""

    token_path = Path("session_token.json")

    def __init__(self, status: dict[str, Any] | None = None) -> None:
        self.sent: list[str] = []
        self._status = status or {}
        self.connected = True
        self.notifications: dict[str, int] = {}
        self.offered: dict[str, dict[str, list[str]]] = {}
        self.replies: dict[str, dict[str, Any]] = {}

    async def connect(self) -> bool:
        return True

    async def send(self, command: str, timeout: float | None = None) -> Response:
        self.sent.append(command)
        return Response(ok=True, code="kOK")

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


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    client = FakeClient(status={"activatedForPlayback": True, "windowMode": "kWriteMode"})
    monkeypatch.setattr(server, "_client", None)
    monkeypatch.setattr(server, "_client_instance", lambda: client)
    monkeypatch.setattr(server, "LogWatch", fake_watch([]))
    return client


# ------------------------------------------------------------------ arithmetic
def test_the_endpoint_folds_slot_and_channel_as_measured() -> None:
    assert playsetup.endpoint(2, 1) == 1024
    assert playsetup.endpoint(3, 1) == 2048
    assert playsetup.endpoint(2, 5) == 1028


def test_slot_one_is_the_click_and_not_addressable() -> None:
    with pytest.raises(ValueError, match="click"):
        playsetup.slot_id(1)


def test_a_value_escapes_commas_and_refuses_query_characters() -> None:
    assert playsetup.value("Allegro, ma non troppo") == "Allegro\\, ma non troppo"
    with pytest.raises(ValueError):
        playsetup.value("a&b")


def test_the_plugin_cache_yields_instruments_with_their_class_id(tmp_path: Path) -> None:
    cache = tmp_path / "vst3plugins.xml"
    cache.write_text(
        "<plugins><plugin><path>/x.vst3</path><vendor>V</vendor>"
        "<class><cid>abc</cid><category>Audio Module Class</category><name>Sampler One</name>"
        "<subCategories>Instrument|Sampler</subCategories></class>"
        "<class><cid>def</cid><category>Component Controller Class</category><name>Sampler One"
        "</name><subCategories>Instrument</subCategories></class></plugin></plugins>",
        encoding="utf-8",
    )
    plugins = playsetup.read_plugins(cache)
    assert [(p.name, p.class_id, p.instrument) for p in plugins] == [("Sampler One", "ABC", True)]
    assert playsetup.find_plugin(plugins, "sampler") == plugins


def test_a_full_instrument_wins_over_a_content_player_of_the_same_name(tmp_path: Path) -> None:
    cache = tmp_path / "vst3plugins.xml"
    classes = "".join(
        f"<class><cid>{cid}</cid><category>Audio Module Class</category><name>{name}</name>"
        f"<subCategories>{cats}</subCategories></class>"
        for cid, name, cats in (("aa", "Sonic", "Instrument|Synth|Sampler"),
                                ("bb", "Sonic", "ContentPlayer"),
                                ("cc", "Strings", "ContentPlayer")))
    cache.write_text(f"<plugins><plugin><path>/s.vst3</path>{classes}</plugin></plugins>",
                     encoding="utf-8")
    plugins = playsetup.read_plugins(cache)
    assert [p.class_id for p in playsetup.find_plugin(plugins, "sonic")] == ["AA"]
    assert all(p.instrument for p in plugins)


# --------------------------------------------------------------------- the log
def test_a_log_watch_sees_only_new_lines_and_the_finishing_line(tmp_path: Path) -> None:
    log = tmp_path / "application.log"
    log.write_text("2026-11-19 10:00:00.000 [info] old line\n", encoding="utf-8")
    watch = applog.LogWatch(log)
    with log.open("a", encoding="utf-8") as handle:
        handle.write("2026-11-19 10:00:01.000 [info] notifyPostCommandExecute: File.Save (5 ms)\n")
    assert watch.lines() == ["notifyPostCommandExecute: File.Save (5 ms)"]
    assert asyncio.run(watch.finished("File.Save", 0.1)) is not None
    assert asyncio.run(watch.finished("File.Revert", 0.1)) is None


# ---------------------------------------------------------------- read requests
class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


async def _query_then_command(replies: list[dict[str, Any]]) -> tuple[Any, Any]:
    client = DoricoClient()
    client._ws = _Socket()
    client._state = ConnectionState.CONNECTED
    client._handshake.set()
    asked = asyncio.create_task(client.query("getflows"))
    await asyncio.sleep(0)
    sent = asyncio.create_task(client.send("Edit.SelectAll"))
    await asyncio.sleep(0)
    for reply in replies:
        await client._handle(json.dumps(reply))
    return await asked, await sent


def test_a_typed_reply_answers_the_request_and_leaves_the_command_its_reply() -> None:
    flows = {"message": "flowslist", "openScoreID": 0, "flows": [{"flowID": 0}]}
    asked, sent = asyncio.run(_query_then_command([flows, {"message": "response", "code": "kOK"}]))
    assert asked.ok and asked.data["flows"] == [{"flowID": 0}]
    assert sent.ok and sent.code == "kOK"


def test_an_error_reply_to_a_request_is_not_taken_by_the_command_after_it() -> None:
    no_score = {"message": "response", "code": "kError", "detail": "kNoScore"}
    command = {"message": "response", "code": "kOK"}
    asked, sent = asyncio.run(_query_then_command([no_score, command]))
    assert not asked.ok and asked.detail == "kNoScore"
    assert sent.ok


# ------------------------------------------------------------------- edition
async def test_a_command_the_edition_does_not_offer_is_refused_unsent(fake: FakeClient) -> None:
    fake.offered = {"Edit.Copy": {"required": [], "optional": []}}
    result = await server.run_command("Edit.Paste")
    assert result["success"] is False
    assert "Elements 6.2.40" in result["error"]
    assert fake.sent == []


async def test_connect_reports_the_edition(fake: FakeClient) -> None:
    result = await server.connect_to_dorico()
    assert result["edition"] == {"variant": "Elements", "number": "6.2.40"}


# ------------------------------------------------------------------- playback
async def test_activate_leaves_an_active_project_alone(fake: FakeClient) -> None:
    result = await server.activate_playback()
    assert result["changed"] is False
    assert fake.sent == []


async def test_activate_sends_the_toggle_only_when_inactive(fake: FakeClient) -> None:
    fake._status["activatedForPlayback"] = False
    await server.activate_playback()
    assert fake.sent == ["Play.ActivateProject"]


async def test_playback_setup_refuses_while_not_activated(fake: FakeClient) -> None:
    fake._status["activatedForPlayback"] = False
    result = await server.playback_setup("route", slot=2, instrument=0)
    assert result["success"] is False
    assert fake.sent == []


async def test_route_sends_the_measured_endpoint(fake: FakeClient) -> None:
    result = await server.playback_setup("route", slot=3, instrument=4, channel=2)
    assert fake.sent == ["Play.SetEndpoint?InstrumentID=4&TechniqueID=&Endpoint=2049&AllFlows=1"]
    assert result["endpoint"] == 2049
    assert result["verified"] is None


async def test_removing_a_slot_needs_confirm(fake: FakeClient) -> None:
    refused = await server.playback_setup("remove_plugin", slot=3)
    assert refused["success"] is False and fake.sent == []
    await server.playback_setup("remove_plugin", slot=3, confirm=True)
    assert fake.sent == ["Play.RemovePlugin?SlotID=2&AlsoDelete=1"]


async def test_an_expression_map_is_resolved_by_name_before_sending(fake: FakeClient) -> None:
    fake.replies["getlibraryentities"] = {
        "libraryentities": [{"entityID": "xmap.cc11_dynamic", "name": "CC11 Dynamics"}]}
    await server.playback_setup("set_expression_map", slot=2, expression_map="cc11 dynamics")
    assert fake.sent == ["Play.SetEndpointExpressionMap?Endpoint=1024&Value=xmap.cc11_dynamic"]
    unknown = await server.playback_setup("set_expression_map", slot=2, expression_map="nope")
    assert unknown["success"] is False


# ---------------------------------------------------------------------- flows
async def test_renaming_a_flow_goes_through_setup_mode_and_back(fake: FakeClient) -> None:
    fake.replies["getflows"] = {"flows": [{"flowID": 1, "flowName": "Flow 2"}]}
    await server.manage_flows("rename", flow=1, name="Coda")
    assert fake.sent == [
        "Window.SwitchMode?WindowMode=kSetupMode",
        "Project.Flow.Rename?FlowID=1&Name=Coda",
        "Window.SwitchMode?WindowMode=kWriteMode",
    ]


async def test_deleting_a_flow_needs_confirm_and_reports_a_waiting_dialog(
    fake: FakeClient,
) -> None:
    fake.replies["getflows"] = {"flows": [{"flowID": 1, "flowName": "Flow 2"}]}
    refused = await server.manage_flows("delete", flow=1)
    assert refused["success"] is False and fake.sent == []
    result = await server.manage_flows("delete", flow=1, confirm=True, wait_seconds=0)
    assert result["success"] is False
    assert "confirm" in result["dialog"]


# -------------------------------------------------------------------- markings
async def test_a_dynamic_is_written_at_the_caret_and_verified(fake: FakeClient) -> None:
    fake._status["selectedEventType"] = "kDynamicGroupEvent"
    result = await server.add_marking("dynamic", "mf", bar=3)
    assert "NoteInput.CreateDynamic?Definition=mf" in fake.sent
    assert fake.sent[-1] == "NoteInput.Exit"
    assert result["verified"] is True


# -------------------------------------------------------------- project files
async def test_closing_without_saving_needs_confirm(fake: FakeClient) -> None:
    fake._status["activeOpenScoreID"] = 2
    refused = await server.project_file("close", save_changes=False)
    assert refused["success"] is False and fake.sent == []
    await server.project_file("close", save_changes=False, confirm=True)
    assert fake.sent == ["File.CloseProject?OpenScoreID=2&Save=false"]


async def test_a_change_refused_while_the_engine_syncs_is_sent_again(
    fake: FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = [Response(ok=False, code="kError", detail="kCommandNotAllowed"),
               Response(ok=True, code="kOK")]

    async def send(command: str, timeout: float | None = None) -> Response:
        fake.sent.append(command)
        return replies.pop(0)

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(fake, "send", send)
    monkeypatch.setattr(server.asyncio, "sleep", no_wait)
    result = await server.playback_setup("enable_plugin", slot=2)
    assert result["success"] is True
    assert fake.sent == ["Play.EnablePlugin?SlotID=1&Value=1"] * 2
