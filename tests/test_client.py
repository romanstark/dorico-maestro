"""Verify the transport's request/reply correlation.

Dorico's replies carry no requestId, so the client matches them to requests in
FIFO order (docs/protocol.md). That makes the queue of pending futures the one
piece of state everything else depends on: a slot left behind by a send that
failed would shift every later reply by one. These tests drive the client against
a stand-in socket, so they need neither a running Dorico nor a real WebSocket.
"""

from __future__ import annotations

from dorico_maestro.client import DoricoClient
from dorico_maestro.models import ConnectionState


class _EchoSocket:
    """A stand-in socket that answers every frame with kOK straight away."""

    def __init__(self, client: DoricoClient) -> None:
        self._client = client
        self.frames: list[str] = []

    async def send(self, payload: str) -> None:
        """Record the frame and hand the client its reply."""
        self.frames.append(payload)
        # The client queues the future before it writes the frame, so by now
        # there is one waiting to be resolved.
        self._client._handle_response({"message": "response", "code": "kOK"})

    async def close(self) -> None:
        """Match the socket interface disconnect() expects."""


def _connected() -> tuple[DoricoClient, _EchoSocket]:
    """Return a client wired to an echo socket, already past the handshake.

    Reaches into private state on purpose: the alternative is a real socket and
    a running Dorico, which is exactly what the unit suite must not require.
    """
    client = DoricoClient()
    client._handshake.set()
    client._state = ConnectionState.CONNECTED
    socket = _EchoSocket(client)
    client._ws = socket
    return client, socket


async def test_a_command_is_sent_and_its_reply_returned() -> None:
    """The reply Dorico correlates with a command comes back from send()."""
    client, socket = _connected()
    response = await client.send("Play.Stop")
    assert response.ok
    assert response.code == "kOK"
    assert len(socket.frames) == 1
    assert "Play.Stop" in socket.frames[0]


async def test_send_many_stops_at_the_first_failure() -> None:
    """Stop sending subsequent commands upon encountering the first failure."""
    client, _ = _connected()

    class _RefusingSocket(_EchoSocket):
        async def send(self, payload: str) -> None:
            self.frames.append(payload)
            self._client._handle_response(
                {"message": "response", "code": "kError", "detail": "kUnknownCommand"}
            )

    client._ws = _RefusingSocket(client)
    results = await client.send_many(["Nope.One", "Nope.Two"])
    assert len(results) == 1
    assert results[0].failed


async def test_a_failed_send_does_not_leave_its_slot_in_the_queue() -> None:
    """Verify that a failed send removes its pending future from the queue."""

    class _BrokenSocket(_EchoSocket):
        async def send(self, payload: str) -> None:
            raise ConnectionResetError("socket went away")

    client, _ = _connected()
    client._ws = _BrokenSocket(client)
    try:
        await client.send("Play.Stop")
    except ConnectionResetError:
        pass
    assert not client._pending
