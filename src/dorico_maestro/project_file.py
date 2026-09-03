"""Read saved .dorico project files without a running Dorico instance.

Dorico project files (.dorico) are ZIP archives containing metadata in
`supplementary_data/scoreinfo/scoreinfo.xml`. This module extracts flow rosters,
player assignments, and document metadata directly from disk.

Verified against Dorico 5.1.60 and 6.2.30.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

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
        return {"success": False, "error": f"{p.name} carries no {SCOREINFO}"}
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
