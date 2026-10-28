"""G-06 (#218): revocar un cliente corta sus sesiones vivas (1008 «revoked»), contra un
uvicorn real con la app completa (lifespan incluido) y un cliente `websockets` real."""

import asyncio
import json
import threading
import time

import httpx
import pytest
import uvicorn
import websockets
from sqlmodel import Session

from src.cli import cmd_deactivate
from src.core.config import settings
from src.db.models import ClientRecord
from src.main import app
from src.services.db_client import db_client
from tests.integration.conftest import (
    ADMIN_TOKEN,
    CLIENT_ID,
    VALID_KEY,
    _configure_app_mocks,
)

HANDSHAKE = {"client_key": VALID_KEY, "input_mode": "text", "output_mode": ["text"]}
INTERVAL_S = 0.2
# La puerta de salida de la issue es ≤ 15 s; con intervalos cortos debe cerrar mucho antes.
CLOSE_DEADLINE_S = 5.0


@pytest.fixture
def server(seed_client, mock_registry, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_REVALIDATE_INTERVAL_S", INTERVAL_S)
    _configure_app_mocks(mock_registry, monkeypatch)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", loop="asyncio", lifespan="on"
    )
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn no arrancó")
        time.sleep(0.01)
    yield srv.servers[0].sockets[0].getsockname()[1]
    srv.should_exit = True
    thread.join(timeout=15)


async def _open(port):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/ws/stream")
    await ws.send(json.dumps(HANDSHAKE))
    ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert ready["type"] == "ready"
    return ws


async def _expect_revoked(ws):
    started = time.monotonic()
    with pytest.raises(websockets.ConnectionClosed) as exc:
        while True:
            await asyncio.wait_for(ws.recv(), timeout=CLOSE_DEADLINE_S)
    elapsed = time.monotonic() - started
    close = exc.value.rcvd
    assert close is not None
    assert (close.code, close.reason) == (1008, "revoked")
    assert elapsed < CLOSE_DEADLINE_S
    return elapsed


def _admin(port):
    return httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", headers={"x-admin-token": ADMIN_TOKEN}
    )


async def _session_status():
    sessions = app.state.session_registry.get_all()
    assert len(sessions) == 1
    return sessions[0].status


@pytest.mark.parametrize("revocation", ["delete", "deactivate", "rotate"])
async def test_revoking_a_client_closes_its_live_session(server, revocation):
    ws = await _open(server)
    async with _admin(server) as admin:
        if revocation == "delete":
            r = await admin.delete(f"/admin/clients/{CLIENT_ID}")
            assert r.status_code == 204
        elif revocation == "deactivate":
            r = await admin.patch(f"/admin/clients/{CLIENT_ID}", json={"is_active": False})
            assert r.status_code == 200
        else:
            r = await admin.post(f"/admin/clients/{CLIENT_ID}/rotate-key")
            assert r.status_code == 200

    await _expect_revoked(ws)
    await asyncio.sleep(0.2)  # deja terminar el teardown del lado del servidor
    assert await _session_status() == "revoked"
    assert app.state.client_registry.get(CLIENT_ID) is None


async def test_cli_deactivation_revokes_within_one_interval(server, db_engine, capsys):
    """#265: sin caché, desactivar desde la CLI (otro proceso, solo toca la BD) corta la
    sesión en la siguiente revalidación, ≤ SESSION_REVALIDATE_INTERVAL_S (+ margen)."""
    ws = await _open(server)
    cmd_deactivate(VALID_KEY, db_engine)
    elapsed = await _expect_revoked(ws)
    assert elapsed < INTERVAL_S + 0.5


async def test_transient_db_error_does_not_close_the_session(server, monkeypatch):
    calls = {"n": 0, "failed": 0}
    real = db_client.get_session

    async def flaky(key):
        calls["n"] += 1
        if calls["n"] in (2, 3):  # la 1.ª es el handshake
            calls["failed"] += 1
            raise RuntimeError("database is locked")
        return await real(key)

    monkeypatch.setattr(db_client, "get_session", flaky)
    ws = await _open(server)
    await asyncio.sleep(INTERVAL_S * 8)  # varias revalidaciones, las 2 primeras fallan
    assert calls["failed"] == 2 and calls["n"] > 3
    assert ws.close_code is None  # sigue abierta

    async with _admin(server) as admin:  # y una revocación posterior sí la corta
        assert (await admin.delete(f"/admin/clients/{CLIENT_ID}")).status_code == 204
    await _expect_revoked(ws)


async def test_healthy_session_stays_open(server):
    ws = await _open(server)
    await asyncio.sleep(INTERVAL_S * 6)
    assert ws.close_code is None
    await ws.close()


def test_defaults():
    from src.core.config import Settings

    assert Settings.model_fields["SESSION_REVALIDATE_INTERVAL_S"].default == 10.0
    assert "SESSION_CACHE_TTL_S" not in Settings.model_fields


async def test_client_recreated_with_same_key_but_new_id_revokes_the_session(server, db_engine):
    """Borrar y recrear con la MISMA clave (otro id) dentro de la ventana: sigue siendo
    otro cliente, la sesión del anterior debe cerrarse."""
    ws = await _open(server)
    with Session(db_engine) as s:
        s.delete(s.get(ClientRecord, CLIENT_ID))
        s.commit()
        s.add(ClientRecord(id="otro-id", name="Nuevo", client_key=VALID_KEY, is_active=True))
        s.commit()
    await _expect_revoked(ws)
