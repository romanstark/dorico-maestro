"""Executor tests using a fake in-memory client without connecting to Dorico."""

from __future__ import annotations

from typing import Any

import pytest

from dorico_maestro.executor import Result, execute, note_input_if_needed
from dorico_maestro.models import Response
from dorico_maestro.registry import Registry
from dorico_maestro.spec import CommandSpec, ParamSpec


class FakeClient:
    """Records every command sent and returns canned responses.

    Stands in for :class:`~dorico_maestro.client.DoricoClient`: it exposes the
    same ``send`` / ``status`` coroutines the executor relies on, but keeps
    everything in memory so tests never touch a socket.
    """

    def __init__(self, *, ok: bool = True, status: dict[str, Any] | None = None) -> None:
        self.sent: list[str] = []
        self._ok = ok
        self._status = status or {}
        self.connected = True

    async def send(self, command: str, timeout: float | None = None) -> Response:
        self.sent.append(command)
        if self._ok:
            return Response(ok=True, code="kOK")
        return Response(ok=False, code="kError", detail="kUnknownCommand")

    async def status(self, wait: float = 2.0) -> dict[str, Any]:
        return dict(self._status)


def _registry(*specs: CommandSpec) -> Registry:
    return Registry(list(specs))


# --------------------------------------------------------------------------- #
# Command-string building
# --------------------------------------------------------------------------- #


async def test_execute_builds_command_string() -> None:
    spec = CommandSpec(
        id="NoteInput.Pitch",
        category="NoteInput",
        params=[
            ParamSpec(name="pitch", dorico="Pitch", required=True),
            ParamSpec(name="octave", dorico="OctaveValue", kind="int", required=True),
        ],
    )
    client = FakeClient()
    result = await execute(client, _registry(spec), "NoteInput.Pitch", pitch="C", octave=4)

    assert client.sent == ["NoteInput.Pitch?Pitch=C&OctaveValue=4"]
    assert result.ok is True
    assert result.command == "NoteInput.Pitch?Pitch=C&OctaveValue=4"
    assert result.code == "kOK"
    assert result.blocked is False


async def test_execute_bare_command() -> None:
    spec = CommandSpec(id="Edit.SelectAll", category="Edit")
    client = FakeClient()
    result = await execute(client, _registry(spec), "Edit.SelectAll")
    assert client.sent == ["Edit.SelectAll"]
    assert result.ok is True


async def test_execute_maps_error_response() -> None:
    spec = CommandSpec(id="Bogus.Command", category="Bogus")
    client = FakeClient(ok=False)
    result = await execute(client, _registry(spec), "Bogus.Command")
    assert result.ok is False
    assert result.code == "kError"
    assert result.detail == "kUnknownCommand"


async def test_execute_unknown_command_raises() -> None:
    client = FakeClient()
    with pytest.raises(KeyError):
        await execute(client, _registry(), "Nope.NotHere")


async def test_execute_invalid_args_raise() -> None:
    spec = CommandSpec(
        id="NoteInput.Pitch",
        category="NoteInput",
        params=[ParamSpec(name="pitch", dorico="Pitch", required=True)],
    )
    client = FakeClient()
    with pytest.raises(ValueError):
        await execute(client, _registry(spec), "NoteInput.Pitch")  # missing required
    assert client.sent == []  # nothing sent when the build fails


# --------------------------------------------------------------------------- #
# Destructive guard
# --------------------------------------------------------------------------- #


async def test_destructive_blocked_without_confirm() -> None:
    spec = CommandSpec(id="Edit.Delete", category="Edit", destructive=True)
    client = FakeClient()
    result = await execute(client, _registry(spec), "Edit.Delete")

    assert result.blocked is True
    assert result.ok is False
    assert result.command == "Edit.Delete"
    assert result.message
    assert client.sent == []  # nothing sent


async def test_destructive_runs_with_confirm() -> None:
    spec = CommandSpec(id="Edit.Delete", category="Edit", destructive=True)
    client = FakeClient()
    result = await execute(client, _registry(spec), "Edit.Delete", confirm=True)

    assert result.blocked is False
    assert result.ok is True
    assert client.sent == ["Edit.Delete"]


# --------------------------------------------------------------------------- #
# Note-input lifecycle
# --------------------------------------------------------------------------- #


async def test_note_input_wraps_command_in_order() -> None:
    spec = CommandSpec(
        id="NoteInput.RestMode",
        category="NoteInput",
        requires_note_input=True,
    )
    client = FakeClient()
    await execute(client, _registry(spec), "NoteInput.RestMode")

    assert client.sent == ["NoteInput.Enter", "NoteInput.RestMode", "NoteInput.Exit"]


async def test_no_note_input_when_not_required() -> None:
    spec = CommandSpec(id="Play.Stop", category="Play")
    client = FakeClient()
    await execute(client, _registry(spec), "Play.Stop")
    assert client.sent == ["Play.Stop"]


async def test_note_input_if_needed_exits_on_error() -> None:
    spec = CommandSpec(id="X.Y", category="X", requires_note_input=True)
    client = FakeClient()
    with pytest.raises(RuntimeError):
        async with note_input_if_needed(client, spec):
            await client.send("X.Y")
            raise RuntimeError("boom")
    # Exit is guaranteed even when the body raises.
    assert client.sent == ["NoteInput.Enter", "X.Y", "NoteInput.Exit"]


async def test_note_input_if_needed_noop_when_not_required() -> None:
    spec = CommandSpec(id="Play.Stop", category="Play")
    client = FakeClient()
    async with note_input_if_needed(client, spec):
        pass
    assert client.sent == []


# --------------------------------------------------------------------------- #
# Verification hook
# --------------------------------------------------------------------------- #


async def test_verify_hook_canundo_alone_is_not_enough() -> None:
    # canUndo is already True after any earlier edit, so it must NOT count as
    # verification on its own; only a note-event selection does.
    spec = CommandSpec(
        id="NoteInput.Pitch",
        category="NoteInput",
        requires_note_input=True,
        verify="note_added",
        params=[
            ParamSpec(name="pitch", dorico="Pitch", required=True),
            ParamSpec(name="octave", dorico="OctaveValue", kind="int", required=True),
        ],
    )
    client = FakeClient(status={"canUndo": True})
    result = await execute(client, _registry(spec), "NoteInput.Pitch", pitch="C", octave=4)
    assert result.verified is False


async def test_verify_hook_passes_from_selected_event_type() -> None:
    spec = CommandSpec(id="X.Y", category="X", verify="note_added")
    client = FakeClient(status={"selectedEventType": "kNoteEvent"})
    result = await execute(client, _registry(spec), "X.Y")
    assert result.verified is True


async def test_verify_hook_fails_when_no_evidence() -> None:
    spec = CommandSpec(id="X.Y", category="X", verify="note_added")
    client = FakeClient(status={})
    result = await execute(client, _registry(spec), "X.Y")
    assert result.verified is False


async def test_no_verify_leaves_verified_none() -> None:
    spec = CommandSpec(id="Play.Stop", category="Play")
    client = FakeClient()
    result = await execute(client, _registry(spec), "Play.Stop")
    assert result.verified is None


# --------------------------------------------------------------------------- #
# Result helpers
# --------------------------------------------------------------------------- #


def test_result_from_response() -> None:
    resp = Response(ok=True, code="kOK", detail=None)
    result = Result.from_response(resp, "Edit.SelectAll")
    assert result.ok is True
    assert result.command == "Edit.SelectAll"
    assert result.code == "kOK"


def test_result_blocked_helper() -> None:
    spec = CommandSpec(id="Edit.Delete", category="Edit", destructive=True)
    result = Result.blocked_(spec, "nope")
    assert result.blocked is True
    assert result.ok is False
    assert result.command == "Edit.Delete"
    assert result.message == "nope"
