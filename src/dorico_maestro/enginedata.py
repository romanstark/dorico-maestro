"""Reader and patcher for the audio engine data inside a .dorico project.

``supplementary_data/vstaudioengine/enginedata`` is a header of two
length-prefixed strings ("MixerData", a version) followed by records: a
length-prefixed name, a u64 payload length and the payload. ``MixerDocument``
is XML naming each hosted plugin (``<instrument id name slot uid>``); each
``PluginState`` is ``u64 id, u32 n, n bytes of VST3 preset, u32 n, n bytes of
preset file name``, where the id matches the XML element.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from xml.etree import ElementTree

ENGINEDATA = "supplementary_data/vstaudioengine/enginedata"


class EngineDataError(ValueError):
    """Raised when engine data does not have the layout this reader knows."""


@dataclass(frozen=True)
class Record:
    name: str
    start: int
    length: int


@dataclass(frozen=True)
class Slot:
    slot: int
    name: str
    class_id: str
    element_id: int


def _string(data: bytes, pos: int) -> tuple[str, int]:
    size = struct.unpack_from("<I", data, pos)[0]
    return data[pos + 4:pos + 4 + size].decode("utf-8", "replace"), pos + 4 + size


def records(data: bytes) -> list[Record]:
    """Return every record after the header, checking that they fill the data exactly."""
    try:
        _, pos = _string(data, 0)
        _, pos = _string(data, pos)
        out: list[Record] = []
        while pos < len(data):
            name, pos = _string(data, pos)
            length = struct.unpack_from("<Q", data, pos)[0]
            pos += 8
            out.append(Record(name, pos, length))
            pos += length
    except struct.error as e:
        raise EngineDataError(f"truncated engine data: {e}") from e
    if pos != len(data):
        raise EngineDataError("engine data records do not end where the data ends")
    return out


def slots(data: bytes) -> list[Slot]:
    """Return the VST instrument slots the engine hosts, from the MixerDocument XML."""
    found = [r for r in records(data) if r.name == "MixerDocument"]
    if not found:
        raise EngineDataError("engine data has no MixerDocument")
    xml = data[found[0].start:found[0].start + found[0].length]
    root = ElementTree.fromstring(xml[xml.index(b"<"):].rstrip(b"\0"))
    return [
        Slot(int(e.get("slot", "0")), e.get("name", ""), (e.get("uid") or "").upper(),
             int(e.get("id", "0")))
        for e in root.iter("instrument")
    ]


def states(data: bytes) -> dict[int, tuple[Record, bytes, str]]:
    """Return each plugin state by element id: record, VST3 preset, preset name."""
    out: dict[int, tuple[Record, bytes, str]] = {}
    for record in records(data):
        if record.name != "PluginState":
            continue
        element = struct.unpack_from("<Q", data, record.start)[0]
        size = struct.unpack_from("<I", data, record.start + 8)[0]
        preset = data[record.start + 12:record.start + 12 + size]
        name, end = _string(data, record.start + 12 + size)
        if end != record.start + record.length or not preset.startswith(b"VST3"):
            raise EngineDataError(f"plugin state {element} does not have the known layout")
        out[element] = (record, preset, name)
    return out


def preset_class(preset: bytes) -> str:
    """Return the class id a VST3 preset file is for."""
    if len(preset) < 48 or not preset.startswith(b"VST3"):
        raise EngineDataError("not a VST3 preset")
    return preset[8:40].decode("ascii", "replace").upper()


def replace_state(data: bytes, slot: int, preset: bytes, preset_name: str) -> bytes:
    """Return engine data with the state of ``slot`` (1-based) replaced by ``preset``.

    Raises:
        EngineDataError: If the slot is missing or the preset is for another plugin.
    """
    hosted = {s.slot: s for s in slots(data)}
    if slot not in hosted:
        raise EngineDataError(f"the engine hosts no plugin in slot {slot}")
    target = hosted[slot]
    if preset_class(preset) != target.class_id:
        raise EngineDataError(
            f"the preset is for class {preset_class(preset)}, slot {slot} holds "
            f"{target.name} ({target.class_id})")
    record, _, _ = states(data)[target.element_id]
    name = preset_name.encode("utf-8")
    payload = (struct.pack("<QI", target.element_id, len(preset)) + preset
               + struct.pack("<I", len(name)) + name)
    head = record.start - 8
    patched = data[:head] + struct.pack("<Q", len(payload)) + payload
    patched += data[record.start + record.length:]
    records(patched)
    return patched
