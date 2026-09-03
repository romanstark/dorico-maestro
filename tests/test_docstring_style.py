"""Validate Python docstring formatting against PEP 257 and project conventions.

Enforces:
- Single-line imperative summary (<= 79 characters).
- No markdown bold markup in docstrings.
- Maximum 2 prose em-dashes per docstring.
- Line length limits (functions <= 40 lines, modules <= 80 lines).
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Maximum summary line length under PEP 257.
MAX_SUMMARY = 79

#: Maximum line count for function docstrings.
MAX_FUNCTION_LINES = 40

#: Maximum line count for module docstrings.
MAX_MODULE_LINES = 80

#: Maximum allowed prose em-dashes per docstring.
MAX_PROSE_EM_DASHES = 2

EM_DASH = "—"

#: Prefixes identifying structured list items or references.
LIST_OPENERS = ("*", "-", ">>>", "|", "#", ":func:", ":class:", ":meth:", ":mod:")


def _modules() -> list[Path]:
    """Every Python file that ships or tests this package."""
    out: list[Path] = []
    for pattern in ("src/**/*.py", "tests/**/*.py", "scripts/**/*.py"):
        out.extend(sorted(ROOT.glob(pattern)))
    return [p for p in out if "__pycache__" not in p.parts]


def _docstrings(path: Path) -> list[tuple[int, str, str, bool]]:
    """Return (line, owner, text, is_module) for every docstring in the file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:  # pragma: no cover - a broken file fails elsewhere
        raise AssertionError(f"{path.name} does not parse: {e}") from e
    found: list[tuple[int, str, str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        text = ast.get_docstring(node, clean=False)
        if text:
            owner = getattr(node, "name", "<module>")
            found.append((getattr(node, "lineno", 1), owner, text, isinstance(node, ast.Module)))
    return found


def _where(path: Path, line: int, owner: str) -> str:
    """A clickable location for a failure message."""
    return f"{path.relative_to(ROOT).as_posix()}:{line} {owner}"


def _prose_em_dashes(text: str) -> int:
    """Em dashes on lines that are prose rather than list items."""
    total = 0
    for line in text.split("\n"):
        if line.strip().startswith(LIST_OPENERS):
            continue
        total += line.count(EM_DASH)
    return total


def test_every_summary_line_is_one_short_line() -> None:
    """PEP 257: a summary that wraps is not a summary."""
    offenders: list[str] = []
    for path in _modules():
        for line, owner, text, _ in _docstrings(path):
            first = text.strip().split("\n")[0].rstrip()
            if len(first) > MAX_SUMMARY:
                offenders.append(
                    f"{_where(path, line, owner)}: summary is {len(first)} chars, "
                    f"max {MAX_SUMMARY}\n      {first[:90]}"
                )
    assert not offenders, "summary lines too long:\n  " + "\n  ".join(offenders)


def test_no_docstring_uses_bold_markers() -> None:
    """Nothing renders markdown in a docstring, so bold is only noise."""
    offenders: list[str] = []
    for path in _modules():
        for line, owner, text, _ in _docstrings(path):
            if "**" in text:
                offenders.append(f"{_where(path, line, owner)}: {text.count('**') // 2} bold runs")
    assert not offenders, (
        "bold markers in docstrings (keep the words, drop the asterisks):\n  "
        + "\n  ".join(offenders)
    )


def test_no_docstring_chains_em_dashes_in_prose() -> None:
    """A sentence held together by dashes wants to be two sentences."""
    offenders: list[str] = []
    for path in _modules():
        for line, owner, text, _ in _docstrings(path):
            count = _prose_em_dashes(text)
            if count > MAX_PROSE_EM_DASHES:
                offenders.append(f"{_where(path, line, owner)}: {count} em dashes in prose")
    assert not offenders, (
        f"more than {MAX_PROSE_EM_DASHES} prose em dashes in one docstring:\n  "
        + "\n  ".join(offenders)
    )


def test_no_docstring_has_grown_into_a_design_document() -> None:
    """Past the cap the prose belongs in docs/ with a pointer left behind."""
    offenders: list[str] = []
    for path in _modules():
        for line, owner, text, is_module in _docstrings(path):
            length = text.count("\n") + 1
            cap = MAX_MODULE_LINES if is_module else MAX_FUNCTION_LINES
            if length > cap:
                kind = "module" if is_module else "function"
                offenders.append(f"{_where(path, line, owner)}: {length} lines, {kind} cap {cap}")
    assert not offenders, "docstrings over their length cap:\n  " + "\n  ".join(offenders)


def test_commands_catalog_docs_style() -> None:
    """Ensure commands.yaml doc fields are concise, clean, and free of markdown noise."""
    import yaml

    catalog_path = ROOT / "src" / "dorico_maestro" / "commands.yaml"
    data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or []

    offenders_bold: list[str] = []
    offenders_em_dash: list[str] = []
    offenders_double_dash: list[str] = []
    offenders_too_long: list[str] = []

    for entry in data:
        cmd_id = entry.get("id", "<unknown>")
        doc = entry.get("doc")
        if not doc:
            continue
        if "**" in doc:
            offenders_bold.append(f"{cmd_id}: contains markdown bold")
        if "—" in doc:
            offenders_em_dash.append(f"{cmd_id}: contains em-dash")
        if "--" in doc:
            offenders_double_dash.append(f"{cmd_id}: contains double hyphen")
        if len(doc) > 300:
            offenders_too_long.append(f"{cmd_id}: length {len(doc)} > 300 chars")

    assert not offenders_bold, (
        "Bold markers in commands.yaml docs:\n  " + "\n  ".join(offenders_bold)
    )
    assert not offenders_em_dash, (
        "Em-dashes in commands.yaml docs:\n  " + "\n  ".join(offenders_em_dash)
    )
    assert not offenders_double_dash, (
        "Double hyphens in commands.yaml docs:\n  " + "\n  ".join(offenders_double_dash)
    )
    assert not offenders_too_long, (
        "Commands.yaml docs exceed 300 chars:\n  " + "\n  ".join(offenders_too_long)
    )

