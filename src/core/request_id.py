"""Gateway-owned correlation IDs for inbound ASGI connections."""
from uuid import uuid4

from starlette.types import ASGIApp, Receive, Scope, Send


class RequestIdMiddleware:
    """Assign an internal request ID to each HTTP request and WebSocket connection."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            scope.setdefault("state", {})["request_id"] = str(uuid4())
        await self.app(scope, receive, send)
