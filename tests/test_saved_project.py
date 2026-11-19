"""score.dtn and engine data: reading, the playback check, patching a slot state."""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Any

import pytest

from dorico_maestro import applog, dtn, enginedata
from dorico_maestro import project_file as pf

BASE = 0xBC


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def make_dtn(tree: Any, base: int = BASE) -> bytes:
    """Encode a tree of dicts, lists and strings the way score.dtn stores it."""
    keys: list[str] = [""]
    values: list[str] = []
    entries: list[bytes] = []

    def index(table: list[str], text: str) -> int:
        if text not in table:
            table.append(text)
        return table.index(text)

    def add(key: str, node: Any) -> int:
        position = len(entries)
        entries.append(b"")
        k = _varint(index(keys, key))
        if isinstance(node, str):
            entries[position] = bytes([base]) + k + _varint(index(values, node))
        elif not node:
            entries[position] = bytes([base | 1]) + k + _varint(0)
        else:
            pairs = node.items() if isinstance(node, dict) else (("entry", v) for v in node)
            children = [add(name, child) for name, child in pairs]
            kind = 2 if isinstance(node, dict) else 3
            entries[position] = (bytes([base | kind]) + k + _varint(0) + _varint(len(children))
                                 + b"".join(_varint(c) for c in children))
        return position

    add("", tree)

    def table(items: list[str]) -> bytes:
        return struct.pack("<I", len(items)) + b"".join(s.encode() + b"\0" for s in items)

    return (struct.pack("<II", 4, 1) + table(keys) + table(values)
            + struct.pack("<I", len(entries)) + b"".join(entries))


def _string(text: str | bytes) -> bytes:
    data = text.encode() if isinstance(text, str) else text
    return struct.pack("<I", len(data)) + data


def _record(name: str, payload: bytes) -> bytes:
    return _string(name) + struct.pack("<Q", len(payload)) + payload


def preset(class_id: str, body: bytes = b"state") -> bytes:
    return b"VST3" + struct.pack("<I", 1) + class_id.encode() + struct.pack("<q", 48) + body


def make_engine(slots: list[tuple[int, str, str, int]], states: dict[int, bytes]) -> bytes:
    xml = "<MixerDocument>" + "".join(
        f'<instrument id="{e}" name="{n}" slot="{s}" uid="{c}"/>' for s, n, c, e in slots
    ) + "</MixerDocument>"
    out = _string("MixerData") + _string("1.0.0") + _record("MixerDocument", xml.encode())
    for element, data in states.items():
        out += _record("PluginState", struct.pack("<QI", element, len(data)) + data + _string(""))
    return out


BEEP, SAMPLER, OTHER = "1" * 32, "A" * 32, "B" * 32


def make_project(path: Path, routes: dict[str, str], engine_sampler: str = SAMPLER) -> Path:
    tree = {
        "info": {"title": "Test"},
        "scorePlayers": {"array": [
            {"playerID": "0", "displayName": "Violins",
             "instruments": [{"instrumentID": "5", "entityID": "instrument.strings.violin"}]},
        ]},
        "flows": {"array": [{"flowID": "0", "flowName": "One"}]},
        "playModeData": {
            "pluginRackContents": {"entries": [
                {"slotID": "0", "pluginID": BEEP, "pluginName": "Beep", "enabled": "true"},
                {"slotID": "1", "pluginID": SAMPLER, "pluginName": "Sampler", "enabled": "true",
                 "programContents": {"entries": [
                     {"channelNumberRel0": "0", "expressionMapID": "xmap.default"}]}},
            ]},
            "instrumentPlaybackData": {"entries": [
                {"instrumentID": iid, "voiceData": [{"techniqueTracks": [{"endpointID": ep}]}]}
                for iid, ep in routes.items()
            ]},
            "projectEngineState": {"syncedEndpoints": "1024"},
        },
    }
    engine = make_engine([(1, "Beep", BEEP, 10), (2, "Sampler", engine_sampler, 20)],
                         {10: preset(BEEP), 20: preset(engine_sampler)})
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("score.dtn", make_dtn(tree))
        archive.writestr(enginedata.ENGINEDATA, engine)
    return path


def test_the_tree_reads_back_whatever_type_byte_base_a_file_uses() -> None:
    tree = {"a": "1", "b": {"array": ["x", "y"]}, "c": {}}
    assert dtn.parse(make_dtn(tree)) == tree
    assert dtn.parse(make_dtn(tree, base=0x7C)) == tree


def test_a_truncated_tree_is_refused() -> None:
    with pytest.raises(dtn.DtnError):
        dtn.parse(make_dtn({"a": "1"})[:-2])


def test_a_healthy_setup_has_no_problems(tmp_path: Path) -> None:
    result = pf.read_playback_setup(make_project(tmp_path / "p.dorico", {"5": "1024"}))
    assert result["problems"] == []
    assert result["instruments"][0]["routes"] == [{"endpoint": 1024, "slot": 2, "channel": 1}]
    assert result["slots"][1]["channels"][0]["expression_map"] == "xmap.default"


def test_a_slot_the_engine_hosts_differently_is_reported(tmp_path: Path) -> None:
    path = make_project(tmp_path / "p.dorico", {"5": "1024"}, engine_sampler=OTHER)
    problems = pf.read_playback_setup(path)["problems"]
    expected = ("slot 2: Dorico lists Sampler, the audio engine hosts Sampler with another "
                f"class id ({OTHER})")
    assert problems == [expected]


def test_a_route_to_an_empty_slot_is_reported(tmp_path: Path) -> None:
    path = make_project(tmp_path / "p.dorico", {"5": "2048"})
    problems = pf.read_playback_setup(path)["problems"]
    assert "where no plugin is loaded" in problems[0]


def test_an_autosave_without_scoreinfo_is_read_from_the_tree(tmp_path: Path) -> None:
    info = pf.read_project_info(make_project(tmp_path / "p.dorico", {"5": "1024"}))
    assert info["source"] == "score.dtn"
    assert info["players"] == ["Violins"] and info["title"] == "Test"


def test_a_state_is_replaced_with_a_backup_and_checked(tmp_path: Path) -> None:
    path = make_project(tmp_path / "p.dorico", {"5": "1024"})
    new = tmp_path / "new.vstpreset"
    new.write_bytes(preset(SAMPLER, b"other state"))
    result = pf.import_plugin_state(path, 2, new, set())
    assert result["success"] is True, result
    assert Path(result["backup"]).is_file()
    out = tmp_path / "out.vstpreset"
    assert pf.export_plugin_state(path, 2, out)["success"]
    assert out.read_bytes() == new.read_bytes()


def test_a_preset_for_another_plugin_or_an_open_project_is_refused(tmp_path: Path) -> None:
    path = make_project(tmp_path / "p.dorico", {"5": "1024"})
    wrong = tmp_path / "wrong.vstpreset"
    wrong.write_bytes(preset(OTHER))
    assert pf.import_plugin_state(path, 2, wrong, set())["success"] is False
    right = tmp_path / "right.vstpreset"
    right.write_bytes(preset(SAMPLER))
    assert pf.import_plugin_state(path, 2, right, {str(path.resolve())})["success"] is False
    assert not list(tmp_path.glob("*before-plugin-state*"))


def test_open_projects_follow_document_sessions(tmp_path: Path) -> None:
    log = tmp_path / "application.log"
    log.write_text("\n".join(f"2026-11-19 10:00:00.000 [info] {line}" for line in (
        "========== Opening file: /a/one.dorico",
        "Creating document session: 0",
        "========== Opening file: /a/two.dorico",
        "Creating document session: 1",
        "Executing command: File.SaveAs?File=/a/three.dorico",
        "Saving document: 1 to /a/three.dorico",
        "Executing command: File.AutoSave",
        "Saving document: 0 to /auto/one.dorico",
        "Closing document session: 0",
    )), encoding="utf-8")
    assert applog.open_projects(log) == {str(Path("/a/three.dorico").resolve())}


def test_an_import_without_a_readable_log_needs_the_caller_to_vouch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dorico_maestro import server

    path = make_project(tmp_path / "p.dorico", {"5": "1024"})
    state = tmp_path / "s.vstpreset"
    state.write_bytes(preset(SAMPLER))
    monkeypatch.setattr(server, "log_path", lambda: None)
    refused = server.plugin_state("import", str(path), 2, str(state), confirm=True)
    assert refused["success"] is False and "dorico_closed" in refused["error"]
    done = server.plugin_state("import", str(path), 2, str(state), confirm=True,
                               dorico_closed=True)
    assert done["success"] is True, done
