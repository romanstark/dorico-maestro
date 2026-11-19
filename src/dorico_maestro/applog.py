"""Reader for Dorico's application log.

A kOK reply only means a command was queued; the log says when it finished
(``notifyPostCommandExecute``), which file a save wrote, and side effects such
as a moved MIDI thru endpoint. It exists only on the machine Dorico runs on.
"""

from __future__ import annotations

import asyncio
import os
import platform
from pathlib import Path

# Written when a command has finished (Dorico Elements 6.2.40).
FINISHED = "notifyPostCommandExecute: "


def log_path() -> Path | None:
    """Return the newest Dorico application log on this machine, or None."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "Steinberg"
    else:
        base = Path.home() / "Library" / "Application Support" / "Steinberg"
    logs = sorted(base.glob("Dorico */application.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


class LogWatch:
    """Lines Dorico appends to its application log after the watch was started."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else log_path()
        self.offset = self.path.stat().st_size if self.path is not None else 0

    @property
    def available(self) -> bool:
        """Return whether there is a log to read on this machine."""
        return self.path is not None

    def lines(self) -> list[str]:
        """Return the message part of every line written since the watch began."""
        if self.path is None:
            return []
        with self.path.open(encoding="utf-8", errors="replace") as handle:
            handle.seek(self.offset)
            text = handle.read()
        return [_message(line) for line in text.splitlines() if line.strip()]

    async def wait_for(self, needle: str, timeout: float, poll: float = 0.25) -> str | None:
        """Return the first new line containing ``needle``, or None after ``timeout`` s."""
        if self.path is None:
            return None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for line in self.lines():
                if needle in line:
                    return line
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(poll)

    async def finished(self, command: str, timeout: float) -> str | None:
        """Return the finishing line for ``command`` (a prefix match), or None."""
        return await self.wait_for(FINISHED + command, timeout)


def _message(line: str) -> str:
    """Return a log line without its timestamp and level."""
    text = line.strip()
    if "] " in text:
        return text.split("] ", 1)[1]
    return text


def open_projects(path: Path | None = None) -> set[str]:
    """Return the project files Dorico has open, from its document session lines."""
    log = path if path is not None else log_path()
    if log is None:
        return set()
    documents: dict[str, str] = {}
    opening = command = ""
    with log.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = _message(line)
            if text.startswith("Executing command: "):
                command = text[len("Executing command: "):].split("?", 1)[0]
            elif text.startswith("========== Opening file: "):
                opening = text[len("========== Opening file: "):]
            elif text.startswith("Creating document session: "):
                documents[text.rsplit(" ", 1)[1]] = opening
            elif text.startswith("Saving document: ") and command == "File.SaveAs":
                number, target = text[len("Saving document: "):].split(" to ", 1)
                documents[number] = target
            elif text.startswith("Closing document session: "):
                documents.pop(text.rsplit(" ", 1)[1], None)
    return {str(Path(p).resolve()) for p in documents.values() if p}
