"""Sync ``commands.yaml`` with the command IDs shipped in a Dorico ``keycommands.json``.

Dorico's ``keycommands.json`` (and the localised ``keycommands_<lang>.json``) is the
authoritative list of command identifiers. This script reads it, extracts every
command ID, and **merges** the result into our ``commands.yaml`` catalog:

* command IDs present in ``keycommands.json`` but missing from the catalog are
  **appended** as ``status: untested`` (namespace inferred from the id);
* it never touches existing entries, so hand-added params / status / flags / docs
  survive untouched (the merge only ever appends);
* command IDs present in the catalog but *absent* from ``keycommands.json`` are
  **reported** (flagged), not removed -- these are stale IDs, or the canonical
  parameterised base commands we keep that the key-command file only ships in
  value-baked form (e.g. ``NoteInput.Pitch``).

The ``keycommands.json`` layout (see ``scripts/extract_catalog.ps1``) nests command
IDs as the *property names* of each ``shortcuts`` entry::

    { "<group>": { "contexts": [ { "shortcuts": [ { "<CommandId>": "<keys>" } ] } ] } }

A recursive fallback also scans for command-shaped strings so the extractor keeps
working if the layout shifts between Dorico versions.

Usage::

    python scripts/sync_catalog.py [keycommands.json] [--catalog commands.yaml] [--dry-run]

Defaults: ``keycommands.json`` -> ``C:\\Program Files\\Steinberg\\Dorico6\\keycommands.json``;
``--catalog`` -> the packaged ``src/dorico_maestro/commands.yaml``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_KEYCOMMANDS = Path(r"C:\Program Files\Steinberg\Dorico6\keycommands.json")
DEFAULT_CATALOG = Path(__file__).resolve().parent.parent / "src" / "dorico_maestro" / "commands.yaml"

# A command id looks like ``Namespace.Command`` (Namespace starts uppercase), with
# optional dotted sub-parts (``Project.Flow.New``) and an optional query string
# (``Play.Stop`` / ``NoteInput.Pitch?Pitch=A``).
_CMD_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)+(?:\?.*)?$")


def category_of(cmd_id: str) -> str:
    """Namespace of a command id (the part before the first '.' or '?')."""
    return cmd_id.split("?", 1)[0].split(".", 1)[0]


def _scan(node: Any, out: set[str]) -> None:
    """Recursively collect command-shaped strings from keys, values and list items."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and _CMD_RE.match(key):
                out.add(key)
            _scan(value, out)
    elif isinstance(node, list):
        for item in node:
            _scan(item, out)
    elif isinstance(node, str) and _CMD_RE.match(node):
        out.add(node)


def extract_ids(data: Any) -> set[str]:
    """Extract every command id from parsed ``keycommands.json`` content."""
    ids: set[str] = set()
    # Structure-aware pass (mirrors extract_catalog.ps1).
    if isinstance(data, dict):
        for group in data.values():
            if isinstance(group, dict) and isinstance(group.get("contexts"), list):
                for ctx in group["contexts"]:
                    if not isinstance(ctx, dict):
                        continue
                    for shortcut in ctx.get("shortcuts") or []:
                        if isinstance(shortcut, dict):
                            ids.update(k for k in shortcut if isinstance(k, str))
    # Recursive fallback for layout drift.
    _scan(data, ids)
    return {i for i in ids if _CMD_RE.match(i)}


def load_catalog_ids(catalog: Path) -> list[str]:
    """Return the command ids already present in ``commands.yaml``, in order."""
    raw = yaml.safe_load(catalog.read_text(encoding="utf-8")) or []
    return [entry["id"] for entry in raw if isinstance(entry, dict) and "id" in entry]


def render_entries(new_ids: list[str]) -> str:
    """Serialise appended entries as YAML text (2-space list indent, quoted ids)."""
    lines = [
        "",
        "  # ==== appended by sync_catalog.py (new ids from keycommands.json) ====",
    ]
    for cmd_id in new_ids:
        lines.append(f"  - id: {json.dumps(cmd_id)}")  # json.dumps == valid YAML dq string
        lines.append(f"    category: {category_of(cmd_id)}")
        lines.append("    status: untested")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("keycommands", nargs="?", default=str(DEFAULT_KEYCOMMANDS),
                        help="path to Dorico's keycommands.json")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG),
                        help="path to commands.yaml to merge into")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    args = parser.parse_args(argv)

    key_path = Path(args.keycommands)
    catalog_path = Path(args.catalog)

    if not key_path.exists():
        print(f"error: keycommands file not found: {key_path}", file=sys.stderr)
        print("       pass the path explicitly, e.g. a keycommands_en.json.", file=sys.stderr)
        return 2
    if not catalog_path.exists():
        print(f"error: catalog not found: {catalog_path}", file=sys.stderr)
        return 2

    try:
        data = json.loads(key_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"error: could not parse {key_path}: {e}", file=sys.stderr)
        return 2

    key_ids = extract_ids(data)
    catalog_ids = load_catalog_ids(catalog_path)
    catalog_set = set(catalog_ids)

    new_ids = sorted(key_ids - catalog_set)
    missing_ids = sorted(catalog_set - key_ids)

    print(f"keycommands : {key_path}")
    print(f"catalog     : {catalog_path}")
    print(f"extracted   : {len(key_ids)} command ids from keycommands.json")
    print(f"catalog has : {len(catalog_ids)} entries")
    print(f"new (append): {len(new_ids)}")
    for cmd_id in new_ids:
        print(f"    + {cmd_id}")
    print(f"missing from keycommands.json (flagged, not removed): {len(missing_ids)}")
    for cmd_id in missing_ids:
        print(f"    ? {cmd_id}")

    if not new_ids:
        print("catalog already covers every keycommands id; nothing appended.")
        return 0

    if args.dry_run:
        print("--dry-run: no changes written.")
        return 0

    text = catalog_path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    catalog_path.write_text(text + render_entries(new_ids), encoding="utf-8", newline="\n")
    print(f"appended {len(new_ids)} entries to {catalog_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
