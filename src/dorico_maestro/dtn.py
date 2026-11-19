"""Reader for score.dtn, the document tree inside a .dorico project.

Layout (Dorico 6.2): ``u32 4, u32 1, u32 key count``, NUL-terminated keys,
``u32 value count``, NUL-terminated values, ``u32 entry count``, then the
entries in pre-order. Each entry is a type byte, a key index and, by type, a
value index (leaf), nothing (empty node), or a child count and the indices of
its child entries (dict or list). The type byte's low two bits give the type;
its upper bits differ between files.
"""

from __future__ import annotations

import struct
from typing import Any

#: Low two bits of an entry's type byte.
LEAF, EMPTY, DICT, LIST = 0, 1, 2, 3


class DtnError(ValueError):
    """Raised when a score.dtn does not have the layout this reader knows."""


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, pos


def _strings(data: bytes, pos: int) -> tuple[list[str], int]:
    count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    out: list[str] = []
    for _ in range(count):
        end = data.index(b"\0", pos)
        out.append(data[pos:end].decode("utf-8", "replace"))
        pos = end + 1
    return out, pos


def parse(data: bytes) -> Any:
    """Return the document tree of a score.dtn as nested dicts, lists and strings.

    Raises:
        DtnError: If the header, the entry count or the length does not match.
    """
    try:
        if struct.unpack_from("<II", data, 0) != (4, 1):
            raise DtnError("unknown score.dtn header")
        keys, pos = _strings(data, 8)
        values, pos = _strings(data, pos)
        count = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        base = data[pos] & 0xFC
        entries: list[tuple[int, int, int, list[int]]] = []
        while pos < len(data):
            tag = data[pos]
            if tag & 0xFC != base:
                raise DtnError(f"unknown entry type {tag:#x} at byte {pos}")
            kind = tag & 3
            key, pos = _varint(data, pos + 1)
            value, pos = _varint(data, pos)
            children: list[int] = []
            if kind in (DICT, LIST):
                size, pos = _varint(data, pos)
                for _ in range(size):
                    child, pos = _varint(data, pos)
                    children.append(child)
            entries.append((kind, key, value, children))
    except (IndexError, struct.error) as e:
        raise DtnError(f"truncated score.dtn: {e}") from e
    if len(entries) != count:
        raise DtnError(f"score.dtn declares {count} entries and holds {len(entries)}")

    def build(index: int) -> Any:
        kind, _, value, children = entries[index]
        if kind == LEAF:
            return values[value]
        if kind == EMPTY:
            return {}
        if kind == DICT:
            return {keys[entries[c][1]]: build(c) for c in children}
        return [build(c) for c in children]

    return build(0)


def items(node: Any) -> list[Any]:
    """Return the elements of a collection node (wrapped as array or entries)."""
    if isinstance(node, list):
        return node
    if isinstance(node, dict):
        for key in ("array", "entries"):
            if isinstance(node.get(key), list):
                return node[key]
    return []
