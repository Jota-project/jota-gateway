"""Single outbound path to a client WebSocket (issue #130, S1).

Every message the gateway sends to a client goes through an `OutboundSender`,
so the order across producer tasks is the enqueue order and a message is never
interleaved with another. A client that stops reading is cut by a per-send timeout (issue #130 S2).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from starlette.websockets import WebSocketDisconnect

from src.core.config import settings

logger = logging.getLogger(__name__)


def _is_routine_disconnect(e: Exception) -> bool:
    """A send on a client that already left (starlette 1.0.0, websockets.py `send`):
    CONNECTED + OSError -> WebSocketDisconnect(1006); DISCONNECTED -> RuntimeError
    'Cannot call "send" once a close message has been sent.' Any other RuntimeError
    (e.g. send before accept) is a gateway bug and stays a WARNING."""
    return isinstance(e, WebSocketDisconnect) or (
        isinstance(e, RuntimeError) and "once a close message has been sent" in str(e)
    )


class ClientGone(ConnectionError):
    """The client socket failed (or the sender was closed); nothing more will be sent."""


class ClientSlow(ClientGone):
    """One send stayed blocked longer than CLIENT_SEND_TIMEOUT_S (issue #130 S2)."""


class OutboundSender(Protocol):
    async def send_json(self, payload: dict) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def flush(self) -> None: ...

    async def aclose(self, timeout: float) -> None: ...

    def queue_stats(self) -> dict: ...


class DirectSender:
    """Passthrough: writes straight to the socket. Default for bridge/tracker."""

    def __init__(self, ws):
        self._ws = ws

    async def send_json(self, payload: dict) -> None:
        await self._ws.send_json(payload)

    async def send_bytes(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def flush(self) -> None:
        return None

    async def aclose(self, timeout: float) -> None:
        return None

    def queue_stats(self) -> dict:
        return {"pending_msgs": 0, "pending_bytes": 0, "peak_msgs": 0, "peak_bytes": 0}


class QueuedSender:
    """FIFO sender: one unbounded queue and one writer task per session.

    `send_*` enqueue and return; the writer performs the real socket write in
    order. Each write runs under `asyncio.timeout(CLIENT_SEND_TIMEOUT_S)`: uvicorn's
    write flow control makes a client that stopped reading block `send`, so a send
    that stays blocked that long marks the client slow (`ClientSlow`), drops what is
    pending and calls `on_slow` once. This is a drain-rate threshold, not a stall
    detector: a client draining far below the rate the gateway writes is cut too, one
    reading at a reasonable rate is not. In practice it only triggers when more data is
    in flight than the OS socket buffers hold (several MB) or the client stops reading;
    measured with a client that reads at playback pace, a 6 MB reply never made a send
    wait. The queue itself stays unbounded, so for a
    client above the threshold it keeps growing for as long as production lasts: the
    timeout bounds memory only for clients below the threshold.
    """

    def __init__(self, ws, on_slow: Callable[[], Awaitable[None]] | None = None):
        self._ws = ws
        self._on_slow = on_slow
        self._queue: asyncio.Queue = asyncio.Queue()
        self._failure: Exception | None = None
        self._closing = False
        # Contadores O(1) de lo encolado y aún no sacado (solo json/bytes; los bytes
        # cuentan solo payloads binarios) y sus máximos (#219).
        self._pending_msgs = 0
        self._pending_bytes = 0
        self._peak_msgs = 0
        self._peak_bytes = 0
        # Not part of JotaBridge.tasks on purpose: close_all() must not cancel it
        # before the final messages (session_end, turn_end) are drained.
        self._writer = asyncio.create_task(self._run(), name="outbound_writer")

    def _check_open(self) -> None:
        if self._failure is not None or self._closing or self._writer.done():
            raise ClientGone("cliente no disponible") from self._failure

    async def send_json(self, payload: dict) -> None:
        self._check_open()
        self._queue.put_nowait(("json", payload))
        self._count_in(0)

    async def send_bytes(self, data: bytes) -> None:
        self._check_open()
        self._queue.put_nowait(("bytes", data))
        self._count_in(len(data))

    def _count_in(self, nbytes: int) -> None:
        self._pending_msgs += 1
        self._pending_bytes += nbytes
        self._peak_msgs = max(self._peak_msgs, self._pending_msgs)
        self._peak_bytes = max(self._peak_bytes, self._pending_bytes)

    def _count_out(self, kind: str, item) -> None:
        self._pending_msgs -= 1
        if kind == "bytes":
            self._pending_bytes -= len(item)

    def queue_stats(self) -> dict:
        return {
            "pending_msgs": self._pending_msgs,
            "pending_bytes": self._pending_bytes,
            "peak_msgs": self._peak_msgs,
            "peak_bytes": self._peak_bytes,
        }

    async def flush(self) -> None:
        """Wait until everything enqueued so far has been written; re-raise failure."""
        self._check_open()
        fut = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(("flush", fut))
        await fut

    async def aclose(self, timeout: float) -> None:
        """Stop accepting messages, drain within `timeout`, stop the writer. Never raises `Exception` (cancellation can propagate)."""
        # Concurrent callers share one writer; routes.py is the only caller.
        self._closing = True
        try:
            if not self._writer.done():
                self._queue.put_nowait(("stop", None))
                try:
                    await asyncio.wait_for(self._writer, timeout=timeout)
                except TimeoutError:
                    # wait_for already cancelled and awaited the writer.
                    logger.warning("Outbound: drenado agotó %gs — descartando lo pendiente.", timeout)
        finally:
            self._discard_pending()

    async def _run(self) -> None:
        while True:
            kind, item = await self._queue.get()
            if kind == "stop":
                return
            if kind == "flush":
                if not item.done():
                    item.set_result(None)
                continue
            self._count_out(kind, item)
            timeout = asyncio.timeout(settings.CLIENT_SEND_TIMEOUT_S)
            try:
                async with timeout:
                    if kind == "json":
                        await self._ws.send_json(item)
                    else:
                        await self._ws.send_bytes(item)
            except Exception as e:
                # TimeoutError is an OSError: only OUR timeout expiring means "slow
                # client"; a send that raises TimeoutError itself is an ordinary failure.
                if timeout.expired():
                    await self._fail_slow()
                    return
                self._failure = e
                # One line, never the payload (it may hold user text).
                level = logging.INFO if _is_routine_disconnect(e) else logging.WARNING
                logger.log(level, "Outbound: fallo de socket (%s) — descartando lo pendiente.",
                           type(e).__name__)
                self._discard_pending()
                return

    async def _fail_slow(self) -> None:
        limit = settings.CLIENT_SEND_TIMEOUT_S
        self._failure = ClientSlow(f"send bloqueado más de {limit}s")
        logger.warning(
            "Outbound: cliente lento (send bloqueado > %gs) — descartando lo pendiente.", limit
        )
        self._discard_pending()
        if self._on_slow is not None:
            try:
                await self._on_slow()
            except Exception as e:
                logger.warning("Outbound: on_slow falló (%s).", type(e).__name__)

    def _discard_pending(self) -> None:
        """Drop queued messages; fail any flush() still waiting on them."""
        while not self._queue.empty():
            kind, item = self._queue.get_nowait()
            if kind in ("json", "bytes"):
                self._count_out(kind, item)
            if kind == "flush" and not item.done():
                item.set_exception(ClientGone("cliente no disponible"))
