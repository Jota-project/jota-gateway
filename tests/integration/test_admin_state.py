"""/admin/state y /admin/state/stream (#219): instantánea viva sin texto ni claves."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.integration.conftest import CLIENT_ID, CLIENT_NAME, VALID_KEY

ROUTES = ["/admin/state", "/admin/state/stream"]

SNAPSHOT_KEYS = {
    "session_id",
    "client_id",
    "client_name",
    "conversation",
    "input_mode",
    "output_mode",
    "started_at",
    "age_s",
    "turn",
    "turn_count",
    "sender",
    "turn_queue",
    "push_backlog",
    "last_activity_s",
    "stt_state",
    "last_latencies",
}


@pytest.mark.parametrize("path", ROUTES)
def test_state_requires_admin_token(client, monkeypatch, path):
    from fogar_gateway.core.config import settings

    assert client.get(path).status_code == 422
    assert client.get(path, headers={"x-admin-token": "wrong"}).status_code == 401
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "")
    assert client.get(path, headers={"x-admin-token": "anything"}).status_code == 503


def test_state_shape_with_active_session(client, admin_headers):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"client_key": VALID_KEY, "input_mode": "text", "output_mode": ["text"]})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "send", "text": "MARCADOR-6g"})
        for _ in range(20):
            if ws.receive_json().get("type") == "turn_end":
                break
        else:
            pytest.fail("sin turn_end")

        r = client.get("/admin/state", headers=admin_headers)

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"gateway", "services", "totals", "sessions"}
    assert set(body["gateway"]) == {"started_at", "uptime_s", "version", "dashboards"}
    assert body["gateway"]["dashboards"] == 0
    from fogar_gateway._version import __version__

    assert body["gateway"]["version"] == __version__
    assert body["services"]["orchestrator"]["state"] == "CONNECTED"
    assert {"provider", "state", "last_error"} <= set(body["services"]["stt"])
    assert {"provider", "state", "last_error"} <= set(body["services"]["tts"])
    assert body["totals"]["sessions_active"] == 1
    assert body["totals"]["sessions_opened"] >= 1

    [row] = [s for s in body["sessions"] if s["client_id"] == CLIENT_ID]
    assert set(row) == SNAPSHOT_KEYS
    assert row["turn_count"] >= 1
    assert row["client_name"] == CLIENT_NAME
    assert row["turn"]["kind"] in ("idle", "normal")
    assert row["input_mode"] == "text"
    assert row["stt_state"] is None
    assert set(row["sender"]) == {"pending_msgs", "pending_bytes", "peak_msgs", "peak_bytes"}
    assert set(row["turn_queue"]) == {"depth", "peak"}
    assert set(row["push_backlog"]) == {"pending", "peak"}
    assert row["started_at"] is not None

    assert "MARCADOR-6g" not in r.text
    assert VALID_KEY not in r.text


async def test_state_stream_emits_and_stops_when_request_reports_disconnect(client, monkeypatch):
    import fogar_gateway.api.admin_routes as admin_routes
    from fogar_gateway.main import app

    monkeypatch.setattr(admin_routes, "STATE_STREAM_INTERVAL_S", 0.01)
    request = SimpleNamespace(app=app, is_disconnected=AsyncMock(side_effect=[False, False, True]))

    resp = await admin_routes.get_state_stream(request)
    assert resp.media_type == "text/event-stream"
    chunks = [c async for c in resp.body_iterator]

    assert len(chunks) == 2
    for c in chunks:
        text = c if isinstance(c, str) else c.decode()
        assert text.startswith("data: ") and text.endswith("\n\n")
        assert "sessions" in json.loads(text[len("data: ") :])
        assert "MARCADOR-6g" not in text and VALID_KEY not in text

    await asyncio.sleep(0.05)
    leftovers = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task()
        and ("state_stream" in t.get_name() or "state_stream" in repr(t.get_coro()))
    ]
    assert leftovers == []


async def test_state_counts_open_streams_for_the_onboard(client, monkeypatch):
    """`gateway.dashboards`: flujos abiertos ahora (el onboard espera a que haya uno)."""
    import fogar_gateway.api.admin_routes as admin_routes
    from fogar_gateway.main import app

    monkeypatch.setattr(admin_routes, "STATE_STREAM_INTERVAL_S", 0.01)
    request = SimpleNamespace(app=app, is_disconnected=AsyncMock(side_effect=[False, False, True]))
    stream = (await admin_routes.get_state_stream(request)).body_iterator

    first = await anext(stream)
    text = first if isinstance(first, str) else first.decode()
    assert json.loads(text[len("data: ") :])["gateway"]["dashboards"] == 1
    assert admin_routes._build_state(app)["gateway"]["dashboards"] == 1
    _ = [c async for c in stream]  # el cliente se va: el contador vuelve a 0
    assert admin_routes._build_state(app)["gateway"]["dashboards"] == 0


async def test_state_stream_ends_by_itself_after_max_lifetime(client, monkeypatch):
    """Sin desconexión: el stream termina solo tras STATE_STREAM_MAX_S (no bloquea el apagado)."""
    import fogar_gateway.api.admin_routes as admin_routes
    from fogar_gateway.main import app

    monkeypatch.setattr(admin_routes, "STATE_STREAM_INTERVAL_S", 0.01)
    monkeypatch.setattr(admin_routes, "STATE_STREAM_MAX_S", 0.05)
    request = SimpleNamespace(app=app, is_disconnected=AsyncMock(return_value=False))

    resp = await admin_routes.get_state_stream(request)

    async def drain():
        return [c async for c in resp.body_iterator]

    chunks = await asyncio.wait_for(drain(), timeout=2.0)
    assert len(chunks) >= 1


def test_state_skips_bridge_whose_snapshot_fails(client, admin_headers):
    from fogar_gateway.main import app

    class _Bad:
        def snapshot(self):
            raise RuntimeError("boom")

    app.state.client_registry.register("bad", _Bad())
    try:
        r = client.get("/admin/state", headers=admin_headers)
    finally:
        app.state.client_registry.unregister("bad", app.state.client_registry.get("bad"))
    assert r.status_code == 200
    assert r.json()["sessions"] == []


# ── Acceso a OpenClaw (M19): permisos, admin, token en uso y emparejamiento ──

ACCESS_KEYS = {"state", "last_error", "scopes", "admin", "auth_method", "pairing",
               "pairing_request_id", "identity_error", "device_id",
               "approve_command", "identity_error_text"}


def test_state_orchestrator_access_before_connecting(client, admin_headers):
    orch = client.get("/admin/state", headers=admin_headers).json()["services"]["orchestrator"]
    assert set(orch) == ACCESS_KEYS
    assert orch["scopes"] is None and orch["admin"] is False
    assert orch["auth_method"] is None and orch["pairing"] == "unknown"
    assert orch["approve_command"] is None and orch["identity_error_text"] is None


def test_state_orchestrator_access_connected_with_admin(client, admin_headers):
    access = client.app.state.openclaw_access
    access.scopes = ["operator.read", "operator.write", "operator.admin"]
    access.auth_method = "device_token"
    access.pairing = "paired"
    access.device_id = "ab" * 32
    orch = client.get("/admin/state", headers=admin_headers).json()["services"]["orchestrator"]
    assert orch["admin"] is True and orch["scopes"][-1] == "operator.admin"
    assert orch["auth_method"] == "device_token" and orch["pairing"] == "paired"
    assert orch["device_id"] == "ab" * 32 and orch["pairing_request_id"] is None


def test_state_orchestrator_pairing_pending(client, admin_headers):
    access = client.app.state.openclaw_access
    access.pairing = "pending"
    access.pairing_request_id = "req-7"
    orch = client.get("/admin/state", headers=admin_headers).json()["services"]["orchestrator"]
    assert orch["pairing"] == "pending" and orch["pairing_request_id"] == "req-7"
    assert orch["admin"] is False
    # La frase y el comando salen del gateway, el mismo texto que la CLI (una sola fuente).
    assert orch["approve_command"] == "openclaw devices approve req-7"


def test_state_orchestrator_identity_required_says_why(client, admin_headers):
    access = client.app.state.openclaw_access
    access.pairing = "identity_required"
    access.identity_error = "challenge_without_nonce"
    orch = client.get("/admin/state", headers=admin_headers).json()["services"]["orchestrator"]
    assert orch["identity_error_text"] == (
        "este OpenClaw no manda nonce en el reto y no hay nada que firmar"
    )
    assert orch["approve_command"] is None


async def test_state_reflects_a_real_pairing_flow(tmp_path, monkeypatch):
    """OpenClawClient real contra el fake remoto; /admin/state lee el mismo objeto de acceso."""
    import fogar_gateway.api.admin_routes as admin_routes
    from fogar_gateway.main import app
    from fogar_gateway.services.openclaw.client import OpenClawClient, OrchestratorPairingRequired
    from fogar_gateway.services.openclaw.device import DeviceStore
    from fogar_gateway.services.openclaw.dispatcher import FrameDispatcher
    from fogar_gateway.services.openclaw.models import OrchestratorAccess
    from fogar_gateway.services.openclaw.registry import ClientRegistry, TurnRegistry
    from fogar_gateway.services.reconnection import ConnectionState, ServiceStatus
    from tests.fakes.openclaw import FakeOpenClawServer

    def status():
        return ServiceStatus("x", ConnectionState.RECONNECTING, None, 1, "orchestrator_pairing_required")

    access = OrchestratorAccess()
    turns = TurnRegistry()
    monkeypatch.setattr(app.state, "openclaw_access", access, raising=False)
    monkeypatch.setattr(app.state, "openclaw", SimpleNamespace(status=status), raising=False)
    voice = SimpleNamespace(name="off", status=status)
    monkeypatch.setattr(app.state, "speech", SimpleNamespace(stt=voice, tts=voice), raising=False)
    monkeypatch.setattr(app.state, "client_registry", ClientRegistry(), raising=False)
    async with FakeOpenClawServer(token="t", remote=True) as server:
        client = OpenClawClient("127.0.0.1", server.port, "t", turns,
                                FrameDispatcher(turns, ClientRegistry()),
                                device_store=DeviceStore(tmp_path / "d.json"), access=access)
        with pytest.raises(OrchestratorPairingRequired):
            await client.connect()
        orch = admin_routes._build_state(app)["services"]["orchestrator"]
        assert orch["pairing"] == "pending" and orch["pairing_request_id"] == "req-1"
        assert orch["last_error"] == "orchestrator_pairing_required"
        server.approve()
        await client.connect()
        orch = admin_routes._build_state(app)["services"]["orchestrator"]
        assert orch["pairing"] == "paired" and orch["admin"] is True
        assert "devtok" not in json.dumps(orch)
        await client.close()  # sin conexión: el acceso vuelve a desconocido
        orch = admin_routes._build_state(app)["services"]["orchestrator"]
        assert orch["pairing"] == "unknown" and orch["admin"] is False
        assert orch["device_id"] is not None
