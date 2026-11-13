import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fogar_gateway.access import verify


class _Fake:
    def __init__(self) -> None:
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self.body = b""
        self.delay = 0.0
        self.url = ""

    def json(self, data: dict) -> None:
        self.body = json.dumps(data).encode()
        self.headers["Content-Type"] = "application/json"

    def redirect(self, location: str) -> None:
        self.status_code = 302
        self.headers["Location"] = location

    def status(self, code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = code
        self.headers.update(headers or {})

    def sleep(self, seconds: float) -> None:
        self.delay = seconds


@pytest.fixture
def fake():
    state = _Fake()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(state.delay)
            self.send_response(state.status_code)
            for k, v in state.headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(state.body)))
            self.end_headers()
            self.wfile.write(state.body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.url = f"http://127.0.0.1:{server.server_port}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield state
    server.shutdown()
    server.server_close()


def test_verify_ok(fake):
    fake.json({"service": "fogar-gateway", "instance_id": "abc"})
    assert verify(fake.url, "abc") == ("ok", "")


def test_verify_other_gateway(fake):
    fake.json({"service": "fogar-gateway", "instance_id": "zzz"})
    assert verify(fake.url, "abc") == ("otro_gateway", "")


def test_verify_not_fogar(fake):
    fake.json({"service": "otra-cosa"})
    assert verify(fake.url, "abc") == ("inalcanzable", "no_es_fogar")


def test_verify_not_json(fake):
    fake.body = b"<html>"
    assert verify(fake.url, "abc") == ("inalcanzable", "no_es_fogar")


def test_verify_cloudflare_access_redirect(fake):
    fake.redirect("https://team.cloudflareaccess.com/cdn-cgi/access/login/x")
    assert verify(fake.url, "abc") == ("protegida", "cloudflare_access")


def test_verify_cloudflare_access_403(fake):
    fake.status(403, {"cf-access-domain": "x"})
    assert verify(fake.url, "abc") == ("protegida", "cloudflare_access")


def test_verify_plain_403_is_unreachable(fake):
    fake.status(403)
    assert verify(fake.url, "abc") == ("inalcanzable", "http_403")


def test_verify_plain_redirect_is_unreachable(fake):
    fake.redirect("https://other/")
    assert verify(fake.url, "abc") == ("inalcanzable", "http_302")


def test_verify_server_error(fake):
    fake.status(502)
    assert verify(fake.url, "abc") == ("inalcanzable", "http_502")


def test_verify_timeout(fake):
    fake.sleep(1)
    assert verify(fake.url, "abc", timeout=0.2) == ("inalcanzable", "timeout")


def test_verify_connection_refused():
    assert verify("http://127.0.0.1:1/", "abc") == ("inalcanzable", "red")


def test_verify_dns():
    assert verify("https://nunca.invalid/", "abc") == ("inalcanzable", "dns")


def test_verify_without_instance_id_does_not_connect():
    assert verify("https://nunca.invalid/", None) == ("sin_verificar", "gateway_parado")


def test_verify_evil_cloudflare_lookalike_is_not_protected(fake):
    fake.redirect("https://evilcloudflareaccess.com/x")
    assert verify(fake.url, "abc") == ("inalcanzable", "http_302")


def test_verify_ignores_proxy_env(fake, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    fake.json({"service": "fogar-gateway", "instance_id": "abc"})
    assert verify(fake.url, "abc") == ("ok", "")


def test_verify_non_http_junk_does_not_raise():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        c, _ = srv.accept()
        c.recv(4096)
        c.sendall(b"\x00garbage not http\r\n\r\n")
        c.close()

    threading.Thread(target=serve, daemon=True).start()
    try:
        assert verify(f"http://127.0.0.1:{srv.getsockname()[1]}/", "abc") == ("inalcanzable", "red")
    finally:
        srv.close()
