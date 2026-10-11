"""#130 S2: captura del transporte y cierre de un cliente lento."""

import asyncio
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from src.core import transport as transport_mod
from src.core.transport import (
    TransportCaptureMiddleware,
    abort_client_transport,
    drop_slow_client,
)


class _Transport:
    def __init__(self):
        self.aborted = 0

    def abort(self):
        self.aborted += 1


class _Proto:
    """Stands in for uvicorn's protocol: its bound `send` is what the ASGI server passes."""

    def __init__(self):
        self.transport = _Transport()

    async def send(self, message):  # pragma: no cover - never called
        pass


async def _plain_send(message):  # pragma: no cover - never called
    pass


async def test_middleware_captures_transport_from_a_bound_send():
    proto = _Proto()
    seen: dict = {}

    async def app(scope, receive, send):
        seen.update(scope["state"])

    await TransportCaptureMiddleware(app)({"type": "websocket"}, None, proto.send)

    assert seen["transport"] is proto.transport


async def test_middleware_stores_none_when_send_is_not_bound_to_a_protocol():
    """TestClient / other ASGI servers: no protocol behind send, so no transport."""
    seen: dict = {}

    async def app(scope, receive, send):
        seen.update(scope["state"])

    await TransportCaptureMiddleware(app)({"type": "websocket"}, None, _plain_send)

    assert seen["transport"] is None


async def test_middleware_ignores_http_scopes():
    seen: list = []

    async def app(scope, receive, send):
        seen.append(dict(scope))

    await TransportCaptureMiddleware(app)({"type": "http"}, None, _Proto().send)

    assert "state" not in seen[0]


def test_abort_client_transport_aborts_and_reports_true():
    t = _Transport()
    ws = SimpleNamespace(scope={"state": {"transport": t}})
    assert abort_client_transport(ws) is True
    assert t.aborted == 1


@pytest.mark.parametrize("scope", [{}, {"state": {}}, {"state": {"transport": None}}])
def test_abort_client_transport_without_transport_returns_false(scope):
    assert abort_client_transport(SimpleNamespace(scope=scope)) is False


def test_abort_client_transport_that_raises_returns_false_and_does_not_propagate(caplog):
    """A failing abort must not propagate: drop_slow_client never raises Exception."""

    class _Broken:
        def abort(self):
            raise RuntimeError("transport roto")

    ws = SimpleNamespace(scope={"state": {"transport": _Broken()}})
    with caplog.at_level("WARNING", logger="src.core.transport"):
        assert abort_client_transport(ws) is False
    assert any("abort falló" in r.getMessage() for r in caplog.records)


async def test_drop_slow_client_survives_an_abort_that_raises():
    class _Broken:
        def abort(self):
            raise RuntimeError("transport roto")

    await drop_slow_client(_WS(_Broken(), _ok))  # must not raise


def test_main_registers_the_transport_capture_middleware():
    """Under TestClient there is no transport either way, so a missing or misplaced
    registration would be invisible: pin it here."""
    from src.main import app

    assert any(m.cls is TransportCaptureMiddleware for m in app.user_middleware)


class _WS:
    def __init__(self, transport, close):
        self.scope = {"state": {"transport": transport, "request_id": "req-1"}}
        self._close = close
        self.closed_with: list = []

    async def close(self, code=1000, reason=None):
        self.closed_with.append((code, reason))
        await self._close()


async def _ok():
    return None


async def _hang():
    await asyncio.Event().wait()


async def _gone():
    raise WebSocketDisconnect(1006)


async def test_drop_slow_client_closes_1013_then_aborts():
    t = _Transport()
    ws = _WS(t, _ok)
    await drop_slow_client(ws)
    assert [c for c, _ in ws.closed_with] == [1013]
    assert t.aborted == 1


async def test_drop_slow_client_aborts_after_the_grace_when_close_hangs(monkeypatch):
    monkeypatch.setattr(transport_mod, "SLOW_CLOSE_GRACE_S", 0.05)
    t = _Transport()
    ws = _WS(t, _hang)
    await asyncio.wait_for(drop_slow_client(ws), timeout=1.0)
    assert t.aborted == 1


async def test_drop_slow_client_aborts_even_if_close_raises():
    t = _Transport()
    await drop_slow_client(_WS(t, _gone))
    assert t.aborted == 1


async def test_drop_slow_client_records_the_event_first_and_survives_a_failing_record():
    t = _Transport()
    order: list = []

    async def record():
        order.append("record")
        raise RuntimeError("tracker roto")

    ws = _WS(t, _ok)
    await drop_slow_client(ws, record=record)
    assert order == ["record"]
    assert t.aborted == 1


async def test_drop_slow_client_still_aborts_when_it_is_cancelled_midway():
    """If aclose()'s timeout cancels the writer while on_slow is running, the callback
    must still abort the transport."""
    t = _Transport()
    ws = _WS(t, _hang)  # close(1013) never returns
    task = asyncio.create_task(drop_slow_client(ws))
    await asyncio.sleep(0.05)  # inside the close
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert t.aborted == 1


async def test_drop_slow_client_without_transport_does_not_raise(caplog):
    """Without a captured transport the drop only logs a WARNING and never raises."""
    ws = _WS(None, _ok)
    with caplog.at_level("WARNING", logger="src.core.transport"):
        await drop_slow_client(ws)
    assert [c for c, _ in ws.closed_with] == [1013]
    assert any(r.levelname == "WARNING" for r in caplog.records)
