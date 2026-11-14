"""Motivo de cierre y rechazos del handshake contados en gateway_stats (#219, 6g)."""

import time
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session
from starlette.websockets import WebSocket as StarletteWebSocket
from starlette.websockets import WebSocketDisconnect

from fogar_gateway.api import routes
from fogar_gateway.core.config import settings
from fogar_gateway.db.models import ClientRecord
from fogar_gateway.main import app
from fogar_gateway.services.stats import gateway_stats
from tests.integration.conftest import CLIENT_ID, VALID_KEY

HANDSHAKE = {"client_key": VALID_KEY, "input_mode": "text", "output_mode": ["text"]}


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _session_end_meta() -> dict | None:
    sessions = app.state.session_registry.get_all()
    assert len(sessions) == 1
    ends = [e for e in sessions[0].events if e.stage == "session_end"]
    return ends[0].meta if ends else None


def test_client_disconnect_reason(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        assert ws.receive_json()["type"] == "ready"
    assert _wait_for(lambda: _session_end_meta() is not None)
    assert _session_end_meta()["reason"] == "client_disconnect"
    assert gateway_stats.snapshot()["sessions_closed"] == {"client_disconnect": 1}
    assert gateway_stats.snapshot()["sessions_opened"] == 1


def test_idle_timeout_reason(client, monkeypatch):
    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.1)
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        assert ws.receive_json()["type"] == "ready"
        assert _wait_for(lambda: _session_end_meta() is not None)
        with pytest.raises(WebSocketDisconnect) as exc:
            while True:
                ws.receive_json()  # session_end y lo que quede, hasta el cierre
    # Inactividad: cierre normal (1000), no un fallo (§13).
    assert (exc.value.code, exc.value.reason) == (1000, "session ended")
    assert _session_end_meta()["reason"] == "idle_timeout"
    assert gateway_stats.snapshot()["sessions_closed"] == {"idle_timeout": 1}


def test_error_reason_on_health_check_1011(client, mock_orchestrator):
    mock_orchestrator.ping = AsyncMock(return_value=False)
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        assert ws.receive_json()["type"] == "status"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert (exc.value.code, exc.value.reason) == (1011, "orchestrator unavailable")
    assert _session_end_meta()["reason"] == "error"
    assert gateway_stats.snapshot()["sessions_closed"] == {"error": 1}
    assert gateway_stats.snapshot()["sessions_opened"] == 1


def test_an_internal_failure_mid_session_closes_1011(client, monkeypatch):
    """Una sesión abierta que termina por un fallo interno cierra con 1011, no con 1000: el
    cliente reconecta con espera, no lo toma por inactividad (§13)."""

    async def broken_run(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(routes.FogarBridge, "run", broken_run)
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        assert ws.receive_json()["type"] == "ready"
        with pytest.raises(WebSocketDisconnect) as exc:
            while True:
                ws.receive_json()
    assert (exc.value.code, exc.value.reason) == (1011, "internal error")
    assert _session_end_meta()["reason"] == "error"


def test_slow_client_reason(client, monkeypatch):
    """on_slow del sender anota slow_client antes de drop_slow_client."""
    captured: list = []

    async def fake_drop(websocket, *, record=None):
        captured.append(True)
        await websocket.close(code=1013)

    monkeypatch.setattr(routes, "drop_slow_client", fake_drop)
    real_sender = routes.QueuedSender

    def _sender(ws, on_slow=None):
        s = real_sender(ws, on_slow=on_slow)
        captured.append(s)
        return s

    monkeypatch.setattr(routes, "QueuedSender", _sender)
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        assert ws.receive_json()["type"] == "ready"
        sender = captured[0]
        client.portal.call(sender._on_slow)
    assert _wait_for(lambda: _session_end_meta() is not None)
    assert _session_end_meta()["reason"] == "slow_client"
    assert gateway_stats.snapshot()["sessions_closed"] == {"slow_client": 1}


def test_handshake_rejections_counted(client, monkeypatch):
    # clave inválida
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({**HANDSHAKE, "client_key": "nope"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert (exc.value.code, exc.value.reason) == (4010, "invalid or inactive credential")
    # JSON inválido
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_text("{no json")
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert (exc.value.code, exc.value.reason) == (4000, "invalid handshake")
    # frame binario
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_bytes(b"\x00\x01")
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert (exc.value.code, exc.value.reason) == (4001, "handshake must be a text frame")
    # demasiado grande
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_text("x" * (settings.HANDSHAKE_MAX_BYTES + 1))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert (exc.value.code, exc.value.reason) == (4002, "handshake too large")
    # agente no permitido
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({**HANDSHAKE, "agent": "no-existe"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert (exc.value.code, exc.value.reason) == (4021, "agent not available")
    # timeout
    monkeypatch.setattr(settings, "HANDSHAKE_TIMEOUT_S", 0.05)
    with client.websocket_connect("/ws/stream") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert (exc.value.code, exc.value.reason) == (4900, "handshake timeout")

    assert gateway_stats.snapshot()["handshake_rejected"] == {
        "invalid_key": 1,
        "invalid": 1,
        "not_text": 1,
        "too_large": 1,
        "agent": 1,
        "timeout": 1,
    }
    assert gateway_stats.snapshot()["sessions_closed"] == {}


# --- Un solo cierre por sesión, y nunca un 1000 tras un fallo --------------------------------


@pytest.fixture
def closes(monkeypatch):
    """Cada close que el gateway intenta mandar, también los que Starlette rechazaría."""
    sent: list = []
    real = StarletteWebSocket.close

    async def spy(self, code=1000, reason=None):
        sent.append((code, reason))
        await real(self, code=code, reason=reason)

    monkeypatch.setattr(StarletteWebSocket, "close", spy)
    return sent


def test_a_setup_failure_without_its_own_close_is_1011_not_1000(client, monkeypatch, closes):
    """Una excepción en el arranque que no manda su cierre (aquí, al tomar la conversación) sale
    como 1011: sin `ready`, la sesión no acabó bien."""

    async def broken_claim(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(routes.FogarBridge, "claim_conversation", broken_claim)
    # La excepción sigue su camino hasta uvicorn (que la registra); el TestClient la relanza al
    # salir. Antes, el cliente ya recibió su cierre.
    with pytest.raises(RuntimeError), client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert (exc.value.code, exc.value.reason) == (1011, "internal error")
    assert closes == [(1011, "internal error")]


def test_a_table_close_is_the_only_close(client, mock_orchestrator, closes):
    """Tras el 1011 del health check, el fin de sesión no intenta un segundo cierre."""
    mock_orchestrator.ping = AsyncMock(return_value=False)
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        assert ws.receive_json()["type"] == "status"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert closes == [(1011, "orchestrator unavailable")]


def test_a_revocation_sends_one_close(client, db_engine, monkeypatch, closes):
    monkeypatch.setattr(settings, "SESSION_REVALIDATE_INTERVAL_S", 0.05)
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        assert ws.receive_json()["type"] == "ready"
        with Session(db_engine) as s:
            rec = s.get(ClientRecord, CLIENT_ID)
            rec.is_active = False
            s.add(rec)
            s.commit()
        with pytest.raises(WebSocketDisconnect) as exc:
            while True:
                ws.receive_json()
    assert (exc.value.code, exc.value.reason) == (4200, "credential revoked")
    assert _wait_for(lambda: _session_end_meta() is not None)
    assert closes == [(4200, "credential revoked")]


def test_an_idle_close_is_the_only_close(client, monkeypatch, closes):
    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.1)
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE)
        assert ws.receive_json()["type"] == "ready"
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_json()
    assert closes == [(1000, "session ended")]
