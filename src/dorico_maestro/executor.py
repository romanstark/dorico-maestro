"""The one generic ``execute()`` and the cross-cutting concerns around it.

Every command runs through :func:`execute`, which is where all shared behaviour
lives exactly once: the destructive-command guard, the note-input lifecycle
(:func:`note_input_if_needed`), sending the built command over the FIFO
transport, mapping the reply into a structured :class:`Result`, and — because
``kOK`` only means *accepted* — running an optional post-condition verifier.

See ``docs/architecture.md`` and ``docs/protocol.md``.
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
    """Structured outcome of running a command through :func:`execute`.

    ``ok`` mirrors Dorico's response code (kOK vs kError). ``verified`` is the
    result of the spec's ``verify`` hook, or ``None`` when there was none.
    ``blocked`` is ``True`` when a destructive command was refused for lack of
    confirmation (nothing was sent); ``message`` then explains why.
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
        """Build a result from a Dorico :class:`Response`."""
        return cls(ok=resp.ok, command=command, code=resp.code, detail=resp.detail)

    @classmethod
    def blocked_(cls, spec: CommandSpec, reason: str) -> Result:
        """A result for a command that was refused before being sent."""
        return cls(ok=False, command=spec.id, blocked=True, message=reason)


@asynccontextmanager
async def note_input_if_needed(client: DoricoClient, spec: CommandSpec) -> AsyncIterator[None]:
    """Enter note input for the block and guarantee exit, if the spec needs it.

    A no-op context for commands that do not require note input. When the spec
    does, ``NoteInput.Enter`` is sent on entry and ``NoteInput.Exit`` on exit —
    the exit runs even if the wrapped body raises.
    """
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
    """Run ``cmd_id`` with ``args`` and return a structured :class:`Result`.

    Looks the spec up in ``registry``, refuses destructive commands unless
    ``confirm=True``, builds and sends the command inside the note-input
    lifecycle if required, then runs the spec's verifier (if any).
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
    """Best-effort check that a note landed, from pushed status.

    ``kOK`` does not prove an effect. ``canUndo`` is too weak — it is already
    ``True`` after any earlier edit, so it cannot confirm a *fresh* note — so we
    require the current selection to be a *note event*, which is what Dorico
    reports right after a pitch is entered at the caret.
    """
    status = await client.status()
    return status.get("selectedEventType") == "kNoteEvent"


# Name -> async post-condition checker. Referenced by ``CommandSpec.verify``.
VERIFIERS: dict[str, Callable[[DoricoClient, dict[str, Any]], Awaitable[bool]]] = {
    "note_added": _verify_note_added,
}
