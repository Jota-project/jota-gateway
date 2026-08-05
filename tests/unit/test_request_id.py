from uuid import UUID

import pytest

from src.core.request_id import RequestIdMiddleware


@pytest.mark.parametrize("scope_type", ["http", "websocket"])
async def test_request_id_middleware_assigns_internal_uuid(scope_type):
    captured = {}

    async def inner(scope, receive, send):
        captured["request_id"] = scope["state"]["request_id"]

    middleware = RequestIdMiddleware(inner)
    scope = {
        "type": scope_type,
        "state": {"existing": "kept"},
        "headers": [(b"x-request-id", b"client-controlled")],
    }

    async def receive():
        return {}

    async def send(message):
        return None

    await middleware(scope, receive, send)

    request_id = captured["request_id"]
    assert UUID(request_id).version == 4
    assert request_id != "client-controlled"
    assert scope["state"]["existing"] == "kept"


def test_request_id_middleware_is_registered_on_app():
    from src.main import app

    assert any(item.cls is RequestIdMiddleware for item in app.user_middleware)
