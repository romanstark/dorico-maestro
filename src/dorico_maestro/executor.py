"""Generic command execution engine and lifecycle management.

Every command runs through execute(), which encapsulates cross-cutting dispatch
behavior: destructive command confirmation checks, note-input lifecycle
management (note_input_if_needed), FIFO transport dispatch, structured
result mapping (Result), and optional post-condition verification.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dorico_maestro.spec import CommandSpec, build

if TYPE_CHECKING:
    from dorico_maestro.client import DoricoClient
    from dorico_maestro.models import Response
    from dorico_maestro.registry import Registry


@dataclass
class Result:
    """Structured outcome of executing a command via execute().

    Attributes:
        ok: True if Dorico returned kOK.
        command: Exact command string dispatched over WebSocket.
        code: Dorico status code string.
        detail: Error detail string if present.
        verified: Outcome of post-condition verifier if configured.
        blocked: True if command was blocked by confirmation guard.
        message: Explanation message when blocked.
    """

    ok: bool
    command: str
    code: str | None = None
    detail: str | None = None
    verified: bool | None = None
    blocked: bool = False
    message: str | None = None

    @classmethod
    def from_response(cls, resp: Response, command: str) -> Result:
        """Construct a Result from a transport Response."""
        return cls(ok=resp.ok, command=command, code=resp.code, detail=resp.detail)

    @classmethod
    def blocked_(cls, spec: CommandSpec, reason: str) -> Result:
        """Construct a Result for a command blocked before transmission."""
        return cls(ok=False, command=spec.id, blocked=True, message=reason)


@asynccontextmanager
async def note_input_if_needed(client: DoricoClient, spec: CommandSpec) -> AsyncIterator[None]:
    """Enter note input if required by spec and guarantee exit on completion."""
    if not spec.requires_note_input:
        yield
        return
    await client.send("NoteInput.Enter")
    try:
        yield
    finally:
        await client.send("NoteInput.Exit")


async def execute(
    client: DoricoClient,
    registry: Registry,
    cmd_id: str,
    *,
    confirm: bool = False,
    **args: object,
) -> Result:
    """Execute a command by ID with arguments and return a structured Result.

    Validates the command specification, checks destructive guards, handles
    note-input lifecycle context, transmits the payload, and executes
    post-condition verification if configured.
    """
    spec = registry.get(cmd_id)
    if spec.destructive and not confirm:
        return Result.blocked_(spec, "destructive command; pass confirm=True to run it")

    command = build(spec, **args)
    async with note_input_if_needed(client, spec):
        resp = await client.send(command)

    result = Result.from_response(resp, command)
    if spec.verify:
        verifier = VERIFIERS.get(spec.verify)
        if verifier is not None:
            result.verified = await verifier(client, args)
    return result


# --------------------------------------------------------------------- verifiers
async def _verify_note_added(client: DoricoClient, args: dict[str, Any]) -> bool:
    """Verify that a note event was created using pushed status deltas."""
    status = await client.status()
    return status.get("selectedEventType") == "kNoteEvent"


# Name -> async post-condition checker. Referenced by ``CommandSpec.verify``.
VERIFIERS: dict[str, Callable[[DoricoClient, dict[str, Any]], Awaitable[bool]]] = {
    "note_added": _verify_note_added,
}
