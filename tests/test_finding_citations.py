"""Validate that measured Dorico findings name the build they came from.

Every claim in docs/protocol.md section 8 rests on a probe against one Dorico
build. Without the build named, a reader cannot tell an Elements measurement
from a Pro-gated one, nor which claims to re-verify after an update. This test
ensures that build citations remain documented for all empirical findings.
"""

from __future__ import annotations

import re
from pathlib import Path

PROTOCOL = Path(__file__).resolve().parent.parent / "docs" / "protocol.md"

#: The section whose bullets carry measurements.
_FINDINGS_HEADING = "## 8. Technical Findings & Workarounds"

#: A finding bullet's title: ``- **Pickup Bar Numbering:**``.
_BULLET = re.compile(r"^- \*\*(.+?):?\*\*", re.MULTILINE)

#: An accepted citation: ``*(Dorico Elements 6.2.30)*`` or ``(Dorico 6.2.30)``.
#: The edition is optional because a few findings hold across tiers; the version
#: is not, since that is what dates the measurement now that no date is written.
_CITATION = re.compile(r"\(Dorico(?: [A-Z][a-z]+)? \d+\.\d+(?:\.\d+)?\)")

#: Bullets that describe this project's own design rather than Dorico's
#: behaviour, with the reason attached. Nothing here reports a probe, so there is
#: no build to name. Anything else added to section 8 needs a citation.
DESIGN_NOT_MEASUREMENT = {
    # Describes how goto_bar composes moves it already documents elsewhere.
    "Caret Dead-Reckoning",
    # Names the commands each marking travels through, from the catalog.
    "Dynamics and Articulations",
}


def _findings() -> dict[str, str]:
    """Map each section 8 bullet title to its body text."""
    text = PROTOCOL.read_text(encoding="utf-8")
    start = text.index(_FINDINGS_HEADING)
    end = text.index("\n## ", start + len(_FINDINGS_HEADING))
    section = text[start:end]
    titles = list(_BULLET.finditer(section))
    out: dict[str, str] = {}
    for i, match in enumerate(titles):
        stop = titles[i + 1].start() if i + 1 < len(titles) else len(section)
        out[match.group(1)] = section[match.start() : stop]
    return out


def test_every_measured_finding_names_the_dorico_build() -> None:
    """Validate that each protocol finding carries a version citation."""
    uncited = [
        title
        for title, body in _findings().items()
        if title not in DESIGN_NOT_MEASUREMENT and not _CITATION.search(body)
    ]
    assert not uncited, (
        "docs/protocol.md section 8 findings with no Dorico build named: "
        + ", ".join(uncited)
        + ". Cite the build as *(Dorico Elements 6.2.30)*, or add the title to "
        "DESIGN_NOT_MEASUREMENT if it reports no probe."
    )


def test_the_exemption_list_stays_a_list_of_real_findings() -> None:
    """Validate that DESIGN_NOT_MEASUREMENT names findings that still exist."""
    stale = DESIGN_NOT_MEASUREMENT - set(_findings())
    assert not stale, (
        f"DESIGN_NOT_MEASUREMENT contains unknown findings: {sorted(stale)}. "
        "All exempted titles must correspond to active findings in docs/protocol.md."
    )


def test_the_findings_section_is_actually_being_read() -> None:
    """Validate that section 8 parses into bullets, so the gate cannot go quiet."""
    findings = _findings()
    assert len(findings) >= 8, (
        f"only {len(findings)} findings parsed out of docs/protocol.md section 8; "
        "verify heading format and bullet structure."
    )
