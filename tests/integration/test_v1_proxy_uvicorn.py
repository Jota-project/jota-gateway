"""G-31 (#216): /v1/* contra un uvicorn REAL con un cliente que simula al reverse proxy.

El peer TCP es siempre 127.0.0.1 (el proxy en el mismo host, dentro de TRUSTED_PROXIES por
defecto). Lo que cambia es qué cabeceras manda. Un proxy que NO fija `X-Real-IP` no puede
convertir a todo Internet en "loopback confiable": sin `X-Real-IP` válido se exige Bearer.
"""

import re
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from src.api.openai_routes import router as openai_router
from src.core.config import settings
from src.core.request_id import RequestIdMiddleware

ROOT = Path(__file__).resolve().parents[2]


def _start(proxy_headers: bool):
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.include_router(openai_router)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        loop="asyncio",
        lifespan="off",
        proxy_headers=proxy_headers,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn no arrancó")
        time.sleep(0.01)
    return server, thread


@pytest.fixture
def live_url(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_LOOPBACK", True)
    monkeypatch.setattr(settings, "TRUSTED_NETWORKS", "")
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1,::1")
    server, thread = _start(proxy_headers=False)
    try:
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _get(url, headers=None):
    return httpx.get(f"{url}/v1/models", headers=headers or {}, timeout=5)


def test_proxy_without_x_real_ip_is_not_trusted(live_url):
    assert _get(live_url).status_code == 401


def test_proxy_with_invalid_x_real_ip_is_not_trusted(live_url):
    assert _get(live_url, {"X-Real-IP": "not-an-ip"}).status_code == 401


def test_proxy_forwarding_external_x_real_ip_is_not_trusted(live_url):
    assert _get(live_url, {"X-Real-IP": "203.0.113.5"}).status_code == 401


def test_x_forwarded_for_alone_never_grants_trust(live_url):
    for xff in ("203.0.113.5", "127.0.0.1"):
        assert _get(live_url, {"X-Forwarded-For": xff}).status_code == 401


def test_proxy_vouching_for_loopback_client_is_trusted(live_url):
    assert _get(live_url, {"X-Real-IP": "127.0.0.1"}).status_code == 200


def test_x_forwarded_for_spoof_of_trusted_network_fails_closed(monkeypatch):
    """Con `--proxy-headers` (default de uvicorn) un proxy que reenvía el XFF del cliente
    deja que el cliente elija el peer; el CMD de la imagen usa `--no-proxy-headers`."""
    monkeypatch.setattr(settings, "TRUSTED_NETWORKS", "192.168.1.0/24")
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1,::1")
    server, thread = _start(proxy_headers=False)
    try:
        port = server.servers[0].sockets[0].getsockname()[1]
        r = httpx.get(
            f"http://127.0.0.1:{port}/v1/models",
            headers={"X-Forwarded-For": "192.168.1.5"},
            timeout=5,
        )
        assert r.status_code == 401
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_dockerfile_disables_proxy_headers_and_caps_ws_size():
    cmd = (ROOT / "Dockerfile").read_text(encoding="utf-8").rsplit("CMD", 1)[1]
    assert "--no-proxy-headers" in cmd
    assert re.search(r"--ws-max-size\s+\d+", cmd)
