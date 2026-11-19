"""Playback setup helpers: slot and endpoint numbers, the engine's plugin list.

Slots are 0-based in commands and 1-based in Dorico's window, slot 1 being the
click; UI slot 2, channel 1 is endpoint 1024 (Dorico Elements 6.2.40).
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

ENDPOINT_STRIDE = 1024

FORBIDDEN = set("&?#")


def slot_id(ui_slot: int) -> int:
    """Return the 0-based ``SlotID`` for a slot number as Dorico's window shows it."""
    if ui_slot < 2:
        raise ValueError("slot numbers start at 2: slot 1 is Dorico's click (DoricoBeep)")
    return ui_slot - 1


def endpoint(ui_slot: int, channel: int) -> int:
    """Return the endpoint for a MIDI channel (1-16) of a slot."""
    if not 1 <= channel <= 16:
        raise ValueError("channel must be 1 to 16")
    return ENDPOINT_STRIDE * slot_id(ui_slot) + channel - 1


def value(text: str) -> str:
    """Return ``text`` as a command value, commas escaped as Dorico logs them."""
    bad = sorted(FORBIDDEN & set(text))
    if bad:
        raise ValueError(f"{text!r} contains {' '.join(bad)}, which a command value cannot carry")
    return text.replace(",", "\\,")


@dataclass(frozen=True)
class Plugin:
    """One installed VST3 class as the audio engine lists it."""

    name: str
    vendor: str
    class_id: str
    instrument: bool
    categories: str
    path: str
    content_player: bool = False


def cache_paths() -> list[Path]:
    """Return the audio engine's VST3 cache files on this machine, newest first."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "Steinberg"
    else:
        base = Path.home() / "Library" / "Preferences"
    found = list(base.glob("Dorico * AudioEngine/*VST3 Cache*/vst3plugins.xml"))
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def read_plugins(path: Path) -> list[Plugin]:
    """Return every audio module class in a ``vst3plugins.xml`` cache file."""
    root = ElementTree.parse(path).getroot()
    out: list[Plugin] = []
    for plugin in root.iter("plugin"):
        location = plugin.findtext("path") or ""
        for cls in plugin.iter("class"):
            if cls.findtext("category") != "Audio Module Class":
                continue
            categories = cls.findtext("subCategories") or ""
            first = categories.split("|")[0]
            out.append(Plugin(
                name=cls.findtext("name") or "",
                vendor=cls.findtext("vendor") or plugin.findtext("vendor") or "",
                class_id=(cls.findtext("cid") or "").upper(),
                instrument=first in ("Instrument", "ContentPlayer"),
                categories=categories,
                path=location,
                content_player=first == "ContentPlayer",
            ))
    return out


def find_plugin(plugins: list[Plugin], name: str) -> list[Plugin]:
    """Return the plugins matching ``name`` or a class id, preferring exact matches.

    One bundle can register a full instrument and a content player under one name
    (HALion Sonic 7.5); an exact name then means the full instrument.
    """
    wanted = name.strip().casefold()
    exact = [p for p in plugins if wanted in (p.name.casefold(), p.class_id.casefold())]
    full = [p for p in exact if not p.content_player]
    if len(exact) > 1 and len(full) == 1:
        return full
    if exact:
        return exact
    return [p for p in plugins if wanted in p.name.casefold()]
