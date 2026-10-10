"""Reaching uvicorn's transport to drop a slow client (issue #130 S2).

A client that stopped reading makes `send` block (uvicorn write flow control) and also
`close()` (the close frame queues behind the data). The only immediate way out is
`transport.abort()`, which Starlette does not expose: the raw ASGI `send` uvicorn passes
down is a method bound to its protocol object, so a pure ASGI middleware can pick the
transport off it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# How long a courtesy close(1013) may take before the transport is aborted. If the
# client resumes reading inside this window it sees 1013; otherwise 1006.
SLOW_CLOSE_GRACE_S = 2.0


class TransportCaptureMiddleware:
    """Store the server transport of each WebSocket in `scope["state"]["transport"]`.

    `None` when `send` is not bound to a protocol (Starlette's TestClient, other ASGI
    servers): callers must tolerate that.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            protocol = getattr(send, "__self__", None)
            scope.setdefault("state", {})["transport"] = getattr(protocol, "transport", None)
        await self.app(scope, receive, send)


def abort_client_transport(websocket: Any) -> bool:
    """Abort the client's transport. False if it was not captured or abort failed."""
    transport = websocket.scope.get("state", {}).get("transport")
    if transport is None:
        return False
    try:
        transport.abort()
    except Exception as e:
        # The middleware stores whatever `.transport` the bound `send.__self__` has.
        logger.warning("abort_client_transport: abort falló (%s).", type(e).__name__)
        return False
    return True


async def drop_slow_client(
    websocket: Any, *, record: Callable[[], Awaitable[object]] | None = None
) -> None:
    """Terminate a client that stopped reading: record, courtesy close(1013), abort.

    Never raises `Exception`. After the abort the server's `receive()` yields
    `websocket.disconnect`, so the session's normal teardown follows.
    """
    request_id = websocket.scope.get("state", {}).get("request_id", "-")
    try:
        if record is not None:
            try:
                await record()
            except Exception as e:
                logger.warning("drop_slow_client: record falló (%s).", type(e).__name__)
        try:
            await asyncio.wait_for(
                websocket.close(code=1013, reason="client too slow"), timeout=SLOW_CLOSE_GRACE_S
            )
        except Exception:
            # TimeoutError (close stuck behind the unread data), WebSocketDisconnect,
            # RuntimeError (already closed): the abort below is the real exit either way.
            pass
    finally:
        # In a `finally` so that if this callback is itself cancelled part-way (e.g.
        # QueuedSender.aclose()'s timeout cancelling the writer inside on_slow), the
        # transport is still aborted: abort() is the only guaranteed way out.
        if abort_client_transport(websocket):
            logger.info(
                "Cliente lento: cierre 1013 y abort del transporte (request_id=%s).", request_id
            )
        else:
            logger.warning(
                "Cliente lento: no hay transporte que abortar (request_id=%s); solo se cerró.",
                request_id,
            )
