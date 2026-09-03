"""Load the command catalog (``commands.yaml``) into :class:`CommandSpec` objects.

The catalog is the single source of truth for which Dorico commands exist, what
parameters they take, and our integration status for each (see
``docs/architecture.md``). The registry loads it once and offers lookups by id
and category plus a status tally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dorico_maestro.models import CmdStatus
from dorico_maestro.spec import CommandSpec, ParamSpec

DEFAULT_CATALOG = Path(__file__).parent / "commands.yaml"


class Registry:
    """An in-memory index of :class:`CommandSpec` objects keyed by command id."""

    def __init__(self, specs: list[CommandSpec]) -> None:
        # dict preserves catalog order for ``all()`` while giving O(1) ``get()``.
        self._by_id: dict[str, CommandSpec] = {s.id: s for s in specs}

    @classmethod
    def load(cls, path: str | None = None) -> Registry:
        """Load specs from a YAML catalog (defaults to the packaged file)."""
        catalog = Path(path) if path else DEFAULT_CATALOG
        raw = yaml.safe_load(catalog.read_text(encoding="utf-8")) or []
        return cls([cls._spec_from_dict(entry) for entry in raw])

    @staticmethod
    def _spec_from_dict(entry: dict[str, Any]) -> CommandSpec:
        cmd_id: str = entry["id"]
        params = [
            ParamSpec(
                name=pd["name"],
                dorico=pd.get("dorico"),
                kind=pd.get("kind", "str"),
                enum=pd.get("enum"),
                required=pd.get("required", False),
            )
            for pd in (entry.get("params") or [])
        ]
        status = entry.get("status")
        return CommandSpec(
            id=cmd_id,
            category=entry.get("category") or cmd_id.split(".", 1)[0],
            params=params,
            status=CmdStatus(status) if status else CmdStatus.UNTESTED,
            requires_note_input=bool(entry.get("requires_note_input", False)),
            destructive=bool(entry.get("destructive", False)),
            verify=entry.get("verify"),
            doc=entry.get("doc", "") or "",
        )

    def get(self, cmd_id: str) -> CommandSpec:
        """Return the spec for ``cmd_id`` or raise :class:`KeyError`."""
        return self._by_id[cmd_id]

    def by_category(self, category: str) -> list[CommandSpec]:
        """All specs in ``category``, in catalog order."""
        return [s for s in self._by_id.values() if s.category == category]

    def all(self) -> list[CommandSpec]:
        """Every spec, in catalog order."""
        return list(self._by_id.values())

    def status_counts(self) -> dict[str, int]:
        """Tally of specs by integration status (all statuses always present)."""
        counts: dict[str, int] = {s.value: 0 for s in CmdStatus}
        for spec in self._by_id.values():
            counts[spec.status.value] += 1
        return counts


_default: Registry | None = None


def default_registry() -> Registry:
    """Return the process-wide registry, loading the packaged catalog once."""
    global _default
    if _default is None:
        _default = Registry.load()
    return _default
