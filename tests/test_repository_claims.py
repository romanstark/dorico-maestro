"""Verify documented command counts and verification quotas against the catalog.

Ensures catalog quotas, verified totals, tool counts, and category breakdowns
documented in README.md, CONTRIBUTING.md, docs/architecture.md, docs/protocol.md,
and server.py match the current command registry and FastMCP tool definitions.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from dorico_maestro.registry import default_registry

ROOT = Path(__file__).resolve().parent.parent

#: Documentation files and modules scanned for catalog claims.
PROSE = (
    "README.md",
    "CONTRIBUTING.md",
    "docs/architecture.md",
    "docs/protocol.md",
    "src/dorico_maestro/server.py",
)

#: Matches category quotas, e.g. `NoteInput` 101 of 125 or `NoteEdit` is 0 of 8.
_CATEGORY_QUOTA = re.compile(r"`(\w+)`\s+(?:is\s+)?(\d+) of (\d+)")

#: Matches total catalog verification: **190 of 348**.
_OVERALL = re.compile(r"\*\*(\d+) of (\d+)\*\*")

#: Matches catalog totals: 348 catalogued / 348 rows / 348 commands.
_TOTAL = re.compile(r"(\d+) (?:\w+ )?(?:catalogued|rows|entries|commands\b)")

#: Matches verified command totals: Verified live (190 commands).
_VERIFIED_TOTAL = re.compile(r"[Vv]erified live \((\d+) commands?\)")


def _counts() -> tuple[int, Counter[str], dict[str, Counter[str]]]:
    rows = default_registry().all()
    per: dict[str, Counter[str]] = {}
    for row in rows:
        per.setdefault(row.id.split(".")[0], Counter())[row.status.value] += 1
    return len(rows), Counter(r.status.value for r in rows), per


def test_every_count_in_prose_matches_the_catalog() -> None:
    """A quota that nobody recomputed is a claim, not a measurement."""
    total, status, per = _counts()
    verified = status["verified"]
    offences: list[str] = []

    for name in PROSE:
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")

        for match in _CATEGORY_QUOTA.finditer(text):
            category, said_v, said_t = match.group(1), int(match.group(2)), int(match.group(3))
            if category not in per:
                continue  # a backticked word that is not a command category
            real_v = per[category].get("verified", 0)
            real_t = sum(per[category].values())
            if (said_v, said_t) != (real_v, real_t):
                line = text[: match.start()].count("\n") + 1
                offences.append(
                    f"{name}:{line}: says {category} is {said_v} of {said_t}; "
                    f"the catalog holds {real_v} of {real_t}"
                )

        for match in _OVERALL.finditer(text):
            said_v, said_t = int(match.group(1)), int(match.group(2))
            if said_t != total:
                continue  # a bold "N of M" about something else
            if said_v != verified:
                line = text[: match.start()].count("\n") + 1
                offences.append(
                    f"{name}:{line}: says {said_v} of {said_t} verified; it is {verified}"
                )

        for match in _VERIFIED_TOTAL.finditer(text):
            said = int(match.group(1))
            if said != verified:
                line = text[: match.start()].count("\n") + 1
                offences.append(
                    f"{name}:{line}: says {said} commands verified live; it is {verified}"
                )

        for match in _TOTAL.finditer(text):
            said = int(match.group(1))
            # Only three-digit figures in this range are talking about THIS catalog;
            # 340 is the raw Dorico ID count and is deliberately different.
            if 300 < said < 400 and said not in (total, 340):
                line = text[: match.start()].count("\n") + 1
                offences.append(f"{name}:{line}: says {said} commands; the catalog holds {total}")

    assert not offences, (
        "count(s) in prose the catalog contradicts:\n  " + "\n  ".join(offences)
        + "\n\nRecompute rather than adjust by hand: every status flip moves these."
    )


def test_the_tool_count_in_prose_matches_the_server() -> None:
    """``27 tools`` is a number that changes whenever a tool is added."""
    server = (ROOT / "src" / "dorico_maestro" / "server.py").read_text(encoding="utf-8")
    real = len(re.findall(r"^@mcp\.tool", server, re.MULTILINE))
    offences = []
    for name in PROSE:
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(\d+) tools\b", text):
            if int(match.group(1)) != real:
                line = text[: match.start()].count("\n") + 1
                offences.append(f"{name}:{line}: says {match.group(1)} tools; there are {real}")
    assert not offences, "\n  ".join(offences)


#: ``**NoteInput (125 rows, 101 verified, 1 unavailable, 23 untested):**`` --- a
#: per-category breakdown. This form slipped past ``_CATEGORY_QUOTA``, which only
#: understands "N of M", and two of these lines were wrong for a day: they still
#: counted rows as ``broken`` after that status had been emptied, and one no
#: longer summed to its own row count.
_CATEGORY_BREAKDOWN = re.compile(r"\*\*(\w+) \((\d+) rows?,\s*([^)]*)\)")

#: The ``12 verified`` pairs inside such a breakdown.
_BREAKDOWN_PART = re.compile(r"(\d+)\s+(verified|reachable|unavailable|broken|untested)")


def test_every_category_breakdown_sums_and_matches_the_catalog() -> None:
    """A breakdown has to add up to its own row count and to the catalog."""
    _, _, per = _counts()
    offences: list[str] = []

    for name in PROSE:
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for match in _CATEGORY_BREAKDOWN.finditer(text):
            category, said_rows, tail = match.group(1), int(match.group(2)), match.group(3)
            if category not in per:
                continue
            line = text[: match.start()].count("\n") + 1
            said = {kind: int(n) for n, kind in _BREAKDOWN_PART.findall(tail)}
            if sum(said.values()) != said_rows:
                offences.append(
                    f"{name}:{line}: {category} breakdown sums to {sum(said.values())}, "
                    f"but the line calls it {said_rows} rows"
                )
            real_rows = sum(per[category].values())
            if said_rows != real_rows:
                offences.append(
                    f"{name}:{line}: {category} is called {said_rows} rows; "
                    f"the catalog holds {real_rows}"
                )
            for kind, count in said.items():
                if per[category].get(kind, 0) != count:
                    offences.append(
                        f"{name}:{line}: {category} is called {count} {kind}; "
                        f"the catalog holds {per[category].get(kind, 0)}"
                    )

    assert not offences, (
        "per-category breakdowns the catalog contradicts:\n  " + "\n  ".join(offences)
    )
