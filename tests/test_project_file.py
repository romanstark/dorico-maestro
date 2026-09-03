"""Tests for offline .dorico project file metadata extraction.

Verifies:
- Flow-level and project-level metadata separation.
- Unset XML elements (<field/>) are omitted rather than reported as empty strings.
- Graceful error handling for missing files, invalid archives, and corrupted XML.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from dorico_maestro.project_file import SCOREINFO, read_project_info

SCOREINFO_XML = """<?xml version="1.0" encoding="utf-8"?>
<Score>
\t<title>Scaler-Chords</title>
\t<subtitle/>
\t<composer/>
\t<lyricist/>
\t<copyright/>
\t<createdDate>Oct 27, 2024, 2:14:57 PM</createdDate>
\t<lastSavedDate>Sep 1, 2026, 9:57:16 PM</lastSavedDate>
\t<createdVersion>5.1.60</createdVersion>
\t<lastSavedVersion>6.2.30</lastSavedVersion>
\t<projectDuration>26.3″</projectDuration>
\t<Players>
\t\t<Player><name>Sopran</name></Player>
\t\t<Player><name>Alt</name></Player>
\t</Players>
\t<Flows>
\t\t<Flow>
\t\t\t<title>Scaler-Chords</title>
\t\t\t<composer/>
\t\t\t<flowDuration>8.0″</flowDuration>
\t\t</Flow>
\t\t<Flow>
\t\t\t<title>Choralphrase in d-Moll</title>
\t\t\t<composer>Music21</composer>
\t\t\t<flowDuration>13.3″</flowDuration>
\t\t</Flow>
\t</Flows>
</Score>
"""


def _project(tmp_path: Path, xml: str | None = SCOREINFO_XML) -> Path:
    """Write a minimal ``.dorico`` container; ``xml=None`` omits the metadata."""
    path = tmp_path / "Scaler-Chords.dorico"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", "<container/>")
        z.writestr("score.dtn", b"\x04\x00\x00\x00kScore\x00")
        if xml is not None:
            z.writestr(SCOREINFO, xml)
    return path


def test_reads_credits_players_and_flows(tmp_path: Path) -> None:
    info = read_project_info(_project(tmp_path))
    assert info["success"] is True
    assert info["title"] == "Scaler-Chords"
    assert info["created_version"] == "5.1.60"
    assert info["last_saved_version"] == "6.2.30"
    assert info["players"] == ["Sopran", "Alt"]
    assert [f["title"] for f in info["flows"]] == ["Scaler-Chords", "Choralphrase in d-Moll"]


def test_a_flow_can_carry_a_credit_the_project_does_not(tmp_path: Path) -> None:
    """Verify flow-level metadata is captured when project-level fields are absent."""
    info = read_project_info(_project(tmp_path))
    assert "composer" not in info, "the project's own composer field is empty here"
    assert info["flows"][1]["composer"] == "Music21"
    assert "composer" not in info["flows"][0]


def test_an_unset_field_is_omitted_not_reported_empty(tmp_path: Path) -> None:
    """Ensure self-closing empty XML elements are omitted from returned dictionary."""
    info = read_project_info(_project(tmp_path))
    for empty in ("subtitle", "composer", "lyricist", "copyright"):
        assert empty not in info, f"{empty} is <{empty}/> in the file and must not appear"


def test_a_missing_file_is_reported_not_raised(tmp_path: Path) -> None:
    info = read_project_info(tmp_path / "absent.dorico")
    assert info["success"] is False
    assert "no such file" in info["error"]


def test_a_non_container_is_reported_not_raised(tmp_path: Path) -> None:
    plain = tmp_path / "score.musicxml"
    plain.write_text("<score-partwise/>", encoding="utf-8")
    info = read_project_info(plain)
    assert info["success"] is False
    assert "not a ZIP container" in info["error"]


def test_a_container_without_metadata_is_reported_not_raised(tmp_path: Path) -> None:
    info = read_project_info(_project(tmp_path, xml=None))
    assert info["success"] is False
    assert SCOREINFO in info["error"]


def test_malformed_metadata_is_reported_not_raised(tmp_path: Path) -> None:
    info = read_project_info(_project(tmp_path, xml="<Score><title>unclosed"))
    assert info["success"] is False
    assert "not valid XML" in info["error"]
