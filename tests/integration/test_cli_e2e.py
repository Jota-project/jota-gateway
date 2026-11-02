"""`jota-gw` de extremo a extremo (M14a, #293): la CLI en otro proceso contra un uvicorn real
con la app entera. Solo habla por /admin, y ninguna salida salvo `add` y `rotate-key` lleva
la clave."""

import asyncio
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
import websockets

from src.core.config import settings
from src.main import app
from tests.integration.conftest import ADMIN_TOKEN, _configure_app_mocks
from tests.integration.test_session_revocation import _expect_revoked

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def server(mock_registry, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_REVALIDATE_INTERVAL_S", 0.2)
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


def _cli(port, *args, json_out=False):
    base = [sys.executable, "-m", "src.cli", "--url", f"http://127.0.0.1:{port}",
            "--token", ADMIN_TOKEN]
    r = subprocess.run(
        [*base, *(["--json"] if json_out else []), *args],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return json.loads(r.stdout) if json_out else r.stdout + r.stderr


async def _open(port, key):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/ws/stream")
    await ws.send(json.dumps({"client_key": key, "input_mode": "text", "output_mode": ["text"]}))
    ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert ready["type"] == "ready"
    return ws


async def test_full_client_lifecycle_through_the_cli(server):
    port = server
    created = _cli(port, "clients", "add", "--name", "Tele", "--type", "web", json_out=True)
    key, cid = created["client_key"], created["id"]

    quiet = [
        _cli(port, "clients", "list"),
        _cli(port, "clients", "show", "tele"),
        _cli(port, "clients", "edit", cid[:8], "--set", "tts_speed=1.5"),
        json.dumps(_cli(port, "clients", "list", json_out=True)),
        _cli(port, "status"),
    ]
    assert _cli(port, "clients", "show", "Tele", json_out=True)["tts_speed"] == 1.5

    ws = await _open(port, key)
    quiet.append(_cli(port, "clients", "disable", "Tele"))
    await _expect_revoked(ws)
    quiet.append(_cli(port, "clients", "enable", "Tele"))

    rotated = _cli(port, "--yes", "clients", "rotate-key", "Tele")
    new_key = next(line.split(": ", 1)[1] for line in rotated.splitlines()
                   if line.startswith("client_key: "))
    assert new_key != key
    ws = await _open(port, new_key)
    await ws.close()

    quiet.append(_cli(port, "--yes", "clients", "remove", "Tele"))
    assert _cli(port, "clients", "list", json_out=True) == []

    for text in quiet:
        assert key not in text and new_key not in text
