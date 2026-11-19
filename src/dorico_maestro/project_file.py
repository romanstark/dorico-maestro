"""Read saved .dorico project files without a running Dorico instance.

Dorico project files (.dorico) are ZIP archives containing metadata in
`supplementary_data/scoreinfo/scoreinfo.xml`. This module extracts flow rosters,
player assignments, and document metadata directly from disk.

Verified against Dorico 5.1.60 and 6.2.30.
"""

from __future__ import annotations

import shutil
import time
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from dorico_maestro import dtn, enginedata

#: Path to metadata XML inside the .dorico ZIP container.
SCOREINFO = "supplementary_data/scoreinfo/scoreinfo.xml"

#: Mapping of XML tag names to snake_case dictionary keys.
_SCORE_FIELDS = {
    "title": "title",
    "subtitle": "subtitle",
    "dedication": "dedication",
    "composer": "composer",
    "arranger": "arranger",
    "lyricist": "lyricist",
    "artist": "artist",
    "copyist": "copyist",
    "publisher": "publisher",
    "editor": "editor",
    "copyright": "copyright",
    "workNumber": "work_number",
    "composerDates": "composer_dates",
    "compositionYear": "composition_year",
    "otherInfo": "other_info",
    "createdDate": "created",
    "lastSavedDate": "last_saved",
    "createdVersion": "created_version",
    "lastSavedVersion": "last_saved_version",
    "projectDuration": "duration",
}


def _text(node: ElementTree.Element | None) -> str | None:
    """Return stripped element text, or None if missing or empty."""
    if node is None or node.text is None:
        return None
    stripped = node.text.strip()
    return stripped or None


def read_project_info(path: str | Path) -> dict[str, Any]:
    """Read metadata, flows, and players from a .dorico project container.

    Args:
        path: Path to the .dorico file on disk.

    Returns:
        Dictionary containing project metadata, player lists, and per-flow
        information, or error details if extraction fails.
    """
    p = Path(path)
    if not p.is_file():
        return {"success": False, "error": f"no such file: {p}"}
    try:
        with zipfile.ZipFile(p) as container:
            raw = container.read(SCOREINFO)
    except zipfile.BadZipFile:
        return {
            "success": False,
            "error": f"{p.name} is not a ZIP container, so it is not a Dorico project file",
        }
    except KeyError:
        try:
            tree, _ = _load(p)
        except (KeyError, ValueError, zipfile.BadZipFile) as e:
            return {"success": False, "error": f"{p.name} carries no {SCOREINFO} ({e})"}
        return _info_from_tree(p, tree)
    except OSError as e:
        return {"success": False, "error": f"could not read {p}: {e}"}

    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as e:
        return {"success": False, "error": f"{SCOREINFO} in {p.name} is not valid XML: {e}"}

    out: dict[str, Any] = {"success": True, "path": str(p)}
    for tag, key in _SCORE_FIELDS.items():
        value = _text(root.find(tag))
        if value is not None:
            out[key] = value

    out["players"] = [
        name for player in root.findall("./Players/Player") if (name := _text(player.find("name")))
    ]

    flows: list[dict[str, Any]] = []
    for flow in root.findall("./Flows/Flow"):
        entry: dict[str, Any] = {}
        for tag, key in _SCORE_FIELDS.items():
            value = _text(flow.find(tag))
            if value is not None:
                entry[key] = value
        duration = _text(flow.find("flowDuration"))
        if duration is not None:
            entry["duration"] = duration
        flows.append(entry)
    out["flows"] = flows
    return out


ENDPOINT_STRIDE = 1024


def _load(path: Path) -> tuple[Any, bytes]:
    with zipfile.ZipFile(path) as container:
        return dtn.parse(container.read("score.dtn")), container.read(enginedata.ENGINEDATA)


def _info_from_tree(path: Path, tree: dict[str, Any]) -> dict[str, Any]:
    """Return read_project_info's answer from score.dtn, for files without scoreinfo."""
    info = tree.get("info") or {}
    out: dict[str, Any] = {"success": True, "path": str(path), "source": "score.dtn"}
    for tag, key in _SCORE_FIELDS.items():
        if info.get(tag):
            out[key] = info[tag]
    out["players"] = [p.get("displayName") for p in dtn.items(tree.get("scorePlayers"))]
    out["flows"] = [{"title": f.get("flowName")} for f in dtn.items(tree.get("flows"))]
    return out


def read_playback_setup(path: str | Path) -> dict[str, Any]:
    """Read the VST slots, routing and expression maps of a saved project.

    Compares Dorico's own record of the rack (score.dtn) with what the audio
    engine hosts (enginedata) and lists every contradiction under ``problems``.
    """
    p = Path(path)
    try:
        tree, engine = _load(p)
        hosted = {s.slot: s for s in enginedata.slots(engine)}
        saved = enginedata.states(engine)
    except (OSError, KeyError, zipfile.BadZipFile, ValueError) as e:
        return {"success": False, "error": f"could not read {p.name}: {e}"}
    play = tree.get("playModeData") or {}
    problems: list[str] = []
    slots: list[dict[str, Any]] = []
    rack_ids: set[int] = set()
    for entry in dtn.items(play.get("pluginRackContents")):
        slot_id = int(entry.get("slotID", -1))
        rack_ids.add(slot_id)
        number = slot_id + 1
        engine = hosted.get(number)
        state = saved.get(engine.element_id) if engine else None
        slots.append({
            "slot": number,
            "plugin": entry.get("pluginName"),
            "class_id": entry.get("pluginID"),
            "enabled": entry.get("enabled") == "true",
            "channels": [
                {"channel": int(c.get("channelNumberRel0", 0)) + 1,
                 "expression_map": c.get("expressionMapID") or None,
                 "program": c.get("programName") or None,
                 "preset_uri": c.get("halionPresetURI") or None}
                for c in dtn.items(entry.get("programContents"))
            ],
            "engine_plugin": engine.name if engine else None,
            "engine_state_bytes": len(state[1]) if state else None,
            "engine_preset_name": (state[2] or None) if state else None,
        })
        if engine is None:
            problems.append(f"slot {number}: Dorico lists {entry.get('pluginName')}, "
                            "the audio engine hosts nothing there")
        elif engine.class_id != str(entry.get("pluginID", "")).upper():
            hosts = engine.name
            if hosts == entry.get("pluginName"):
                hosts += f" with another class id ({engine.class_id})"
            problems.append(f"slot {number}: Dorico lists {entry.get('pluginName')}, "
                            f"the audio engine hosts {hosts}")
    for number, engine in sorted(hosted.items()):
        if number - 1 not in rack_ids:
            problems.append(f"slot {number}: the audio engine hosts {engine.name}, "
                            "which Dorico's rack does not list")
    synced_text = (play.get("projectEngineState") or {}).get("syncedEndpoints") or ""
    synced = {int(x) for x in synced_text.split(",") if x.strip().isdigit()}
    names = {
        i.get("instrumentID"): (pl.get("displayName"), i.get("entityID"), pl.get("playerID"))
        for pl in dtn.items(tree.get("scorePlayers")) for i in dtn.items(pl.get("instruments"))
    }
    instruments: list[dict[str, Any]] = []
    for entry in dtn.items((play.get("instrumentPlaybackData") or {}).get("entries")):
        iid = entry.get("instrumentID")
        endpoints = sorted({
            int(t.get("endpointID", -1))
            for v in dtn.items(entry.get("voiceData")) for t in dtn.items(v.get("techniqueTracks"))
        } - {-1})
        player, entity, player_id = names.get(iid, (None, entry.get("instrumentEntityID"), None))
        instruments.append({
            "instrument_id": int(iid) if str(iid).isdigit() else iid,
            "player": player,
            "player_id": int(player_id) if str(player_id).isdigit() else None,
            "instrument": entity,
            "routes": [{"endpoint": e, "slot": e // ENDPOINT_STRIDE + 1,
                        "channel": e % ENDPOINT_STRIDE + 1} for e in endpoints],
        })
        for e in endpoints:
            if e // ENDPOINT_STRIDE not in rack_ids:
                problems.append(f"{player or iid}: routed to endpoint {e} (slot "
                                f"{e // ENDPOINT_STRIDE + 1}), where no plugin is loaded")
            elif synced and e not in synced:
                problems.append(f"{player or iid}: endpoint {e} is not synced with the "
                                "audio engine")
    return {
        "success": True,
        "path": str(p),
        "slots": slots,
        "instruments": instruments,
        "synced_endpoints": sorted(synced),
        "problems": problems,
    }


def export_plugin_state(path: str | Path, slot: int, preset_path: str | Path) -> dict[str, Any]:
    """Write the saved state of a slot (1-based, as Dorico shows it) as a .vstpreset."""
    p, out = Path(path), Path(preset_path)
    try:
        _, engine = _load(p)
        hosted = {s.slot: s for s in enginedata.slots(engine)}
        if slot not in hosted:
            return {"success": False, "error": f"the audio engine hosts nothing in slot {slot}"}
        _, preset, name = enginedata.states(engine)[hosted[slot].element_id]
        out.write_bytes(preset)
    except (OSError, KeyError, zipfile.BadZipFile, ValueError) as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "slot": slot, "plugin": hosted[slot].name, "preset": str(out),
            "bytes": len(preset), "preset_name": name or None}


def import_plugin_state(
    path: str | Path, slot: int, preset_path: str | Path, open_projects: set[str]
) -> dict[str, Any]:
    """Replace the saved state of a slot with a .vstpreset, keeping a backup copy.

    The project must be closed in Dorico, which would otherwise overwrite the
    file on its next save; ``open_projects`` names the files Dorico has open.
    """
    p, src = Path(path).resolve(), Path(preset_path)
    if str(p) in open_projects:
        return {"success": False, "error": f"{p.name} is open in Dorico; close it first"}
    try:
        preset = src.read_bytes()
        with zipfile.ZipFile(p) as container:
            entries = [(i, container.read(i.filename)) for i in container.infolist()]
        engine = next(data for info, data in entries if info.filename == enginedata.ENGINEDATA)
        patched = enginedata.replace_state(engine, slot, preset, src.name)
        backup = p.with_name(f"{p.stem}.before-plugin-state-{int(time.time())}{p.suffix}")
        shutil.copy2(p, backup)
        temp = p.with_name(p.name + ".tmp")
        with zipfile.ZipFile(temp, "w") as target:
            for info, data in entries:
                target.writestr(info, patched if info.filename == enginedata.ENGINEDATA else data)
        with zipfile.ZipFile(temp) as check:
            if check.testzip() is not None:
                raise OSError("the rewritten project failed its zip test")
            enginedata.states(check.read(enginedata.ENGINEDATA))
        temp.replace(p)
    except (OSError, StopIteration, zipfile.BadZipFile, ValueError) as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "slot": slot, "preset": str(src), "bytes": len(preset),
            "backup": str(backup)}
