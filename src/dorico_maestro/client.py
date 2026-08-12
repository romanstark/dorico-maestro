"""WebSocket client for Dorico's Remote Control API.

Implements the connection protocol from ``docs/protocol.md``:

* connect over IPv4 ``127.0.0.1`` (never ``localhost`` — dead IPv6 ``::1`` on Windows)
* session-token handshake, including the first-connect approval dialog
* **FIFO** response correlation (Dorico replies carry no ``requestId``)
* a merged snapshot of the *pushed* status (there is no ``Application.Status``)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Self

import websockets

from dorico_maestro.models import ConnectionState, Response

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORTS = (4560, 4561, 4562, 4563, 4564, 4565)
HANDSHAKE_VERSION = "1.0"
ERROR_CODES = {"kError", "kUnknownCommand", "kInvalidCommand", "kFail"}


class DoricoConnectionError(Exception):
    """Raised when a connection to Dorico cannot be established."""


class DoricoClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int | None = None,
        client_name: str = "Dorico Maestro",
        connect_timeout: float = 6.0,
        command_timeout: float = 20.0,
        approval_timeout: float = 60.0,
    ) -> None:
        self.host = host
        self.port = port
        self.client_name = client_name
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.approval_timeout = approval_timeout

        self._ws: Any = None
        self._state = ConnectionState.DISCONNECTED
        self._pending: deque[asyncio.Future[Response]] = deque()
        self._status: dict[str, Any] = {}
        self._status_event = asyncio.Event()
        self._handshake = asyncio.Event()
        self._handshake_error: str | None = None
        self._recv_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED

    @property
    def token_path(self) -> Path:
        if platform.system() == "Windows":
            base = Path(os.environ.get("APPDATA", str(Path.home())))
        else:
            base = Path.home() / ".config"
        return base / "dorico-maestro" / "session_token.json"

    # ------------------------------------------------------------- lifecycle
    async def connect(self) -> bool:
        if self.connected:
            return True
        self._state = ConnectionState.CONNECTING
        port = self.port or await self._discover_port()
        if port is None:
            self._state = ConnectionState.ERROR
            raise DoricoConnectionError(
                f"Could not find Dorico on {self.host}:{DEFAULT_PORTS}. "
                "Is Dorico running with Remote Control enabled?"
            )
        self.port = port
        uri = f"ws://{self.host}:{port}"
        try:
            self._ws = await asyncio.wait_for(websockets.connect(uri), self.connect_timeout)
        except Exception as e:
            self._state = ConnectionState.ERROR
            raise DoricoConnectionError(f"Failed to open {uri}: {e}") from e

        self._recv_task = asyncio.create_task(self._recv_loop())
        await self._do_handshake()
        self._state = ConnectionState.CONNECTED
        logger.info("Connected to Dorico on %s", uri)
        return True

    async def disconnect(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            self._recv_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        self._state = ConnectionState.DISCONNECTED

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    # -------------------------------------------------------------- commands
    async def send(self, command: str, timeout: float | None = None) -> Response:
        """Send one command and await its reply (FIFO-correlated)."""
        if not self.connected:
            await self.connect()
        if self._ws is None:
            raise DoricoConnectionError("Not connected")

        fut: asyncio.Future[Response] = asyncio.get_running_loop().create_future()
        self._pending.append(fut)
        payload = json.dumps(
            {"message": "command", "command": command, "requestId": uuid.uuid4().hex[:8]}
        )
        try:
            await self._ws.send(payload)
            return await asyncio.wait_for(fut, timeout or self.command_timeout)
        except Exception:
            try:
                self._pending.remove(fut)
            except ValueError:
                pass
            raise

    async def send_many(self, commands: list[str]) -> list[Response]:
        """Send commands in order, stopping at the first failure."""
        out: list[Response] = []
        for c in commands:
            r = await self.send(c)
            out.append(r)
            if r.failed:
                break
        return out

    async def status(self, wait: float = 2.0) -> dict[str, Any]:
        """Return the merged snapshot of Dorico's pushed status."""
        if not self._status_event.is_set():
            try:
                await asyncio.wait_for(self._status_event.wait(), wait)
            except TimeoutError:
                pass
        return dict(self._status)

    # -------------------------------------------------------------- internals
    async def _discover_port(self) -> int | None:
        for port in DEFAULT_PORTS:
            uri = f"ws://{self.host}:{port}"
            try:
                ws = await asyncio.wait_for(websockets.connect(uri), 2.0)
                await ws.close()
                return port
            except Exception:  # noqa: BLE001, S112 - probing ports; failures are expected
                continue
        return None

    async def _do_handshake(self) -> None:
        assert self._ws is not None
        self._handshake.clear()
        self._handshake_error = None
        msg: dict[str, str] = {
            "message": "connect",
            "clientName": self.client_name,
            "handshakeVersion": HANDSHAKE_VERSION,
        }
        token = self._load_token()
        if token:
            msg["sessionToken"] = token
        self._state = ConnectionState.AWAITING_APPROVAL
        await self._ws.send(json.dumps(msg))
        try:
            await asyncio.wait_for(self._handshake.wait(), self.approval_timeout)
        except TimeoutError as e:
            raise DoricoConnectionError(
                "Timed out waiting for Dorico to approve the connection"
            ) from e
        if self._handshake_error:
            raise DoricoConnectionError(self._handshake_error)

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                await self._handle(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("receive loop ended: %s", e)
            self._state = ConnectionState.DISCONNECTED

    async def _handle(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("non-JSON message: %r", raw[:120])
            return

        mtype = data.get("message")
        if mtype == "sessiontoken":
            token = data.get("sessionToken")
            if token and self._ws is not None:
                self._save_token(token)
                await self._ws.send(
                    json.dumps({"message": "acceptsessiontoken", "sessionToken": token})
                )
        elif mtype == "response":
            self._handle_response(data)
        elif mtype == "status":
            self._status.update(data)  # deltas -> merge
            self._status_event.set()
        # selectionchanged / documentchanged / playback* -> not handled yet

    def _handle_response(self, data: dict[str, Any]) -> None:
        code = data.get("code")
        # The first response after connect is the handshake result.
        if not self._handshake.is_set():
            if code == "kConnected":
                self._handshake.set()
            else:
                self._handshake_error = data.get("detail") or code or "handshake failed"
                self._handshake.set()
            return
        # Command replies carry no requestId -> correlate FIFO.
        is_err = code in ERROR_CODES or data.get("detail") in ERROR_CODES
        if self._pending:
            fut = self._pending.popleft()
            if not fut.done():
                fut.set_result(
                    Response(ok=not is_err, code=code, detail=data.get("detail"), data=data.get("data"))
                )
        else:
            logger.debug("unmatched response: %s", data)

    def _load_token(self) -> str | None:
        try:
            if self.token_path.exists():
                return json.loads(self.token_path.read_text()).get("token")
        except Exception:  # noqa: BLE001, S110 - missing/corrupt token is non-fatal
            pass
        return None

    def _save_token(self, token: str) -> None:
        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(json.dumps({"token": token}))
        except Exception as e:  # noqa: BLE001
            logger.warning("could not save session token: %s", e)
