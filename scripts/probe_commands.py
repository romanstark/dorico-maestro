"""Probe whether Dorico command IDs are real (kOK) or unknown (kUnknownCommand).

Usage:
    python scripts/probe_commands.py "Edit.SelectAll" "Play.Stop"

Requires Dorico running with Remote Control enabled. Only pass NON-destructive
commands when a real score is open, or use a throwaway/scratch project. Never
blind-probe Edit.Delete, File.Save/Close, transpose or Add* on work you care about.
"""

import asyncio
import sys

from dorico_maestro.client import DoricoClient
from dorico_maestro.registry import default_registry


def catalogued_status(cmd: str) -> str:
    """Return the catalogued status of a command.

    Queries default_registry for the command status, or returns
    'not in catalog' if the command ID is absent.
    """
    try:
        return default_registry().get(cmd).status.value
    except KeyError:
        return "not in catalog"


async def main(commands: list[str]) -> None:
    if not commands:
        print(__doc__)
        return
    client = DoricoClient()
    await client.connect()
    print(f"connected: {client.state.value} (port {client.port})")
    for cmd in commands:
        r = await client.send(cmd)
        tag = "OK  " if r.ok else "FAIL"
        print(
            f"  [{tag}] {cmd}  code={r.code} detail={r.detail}  "
            f"(catalog: {catalogued_status(cmd)})"
        )
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
