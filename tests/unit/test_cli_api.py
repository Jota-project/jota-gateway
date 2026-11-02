"""Cliente HTTP de la CLI contra /admin (M14a, #293)."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from src.cli import api


class _Fake:
    """Respuesta configurable y registro de la última petición."""

    status = 200
    body: ClassVar[object] = {"ok": True}
    delay = 0.0
    last: ClassVar[dict] = {}


class _Handler(BaseHTTPRequestHandler):
    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        _Fake.last = {
            "method": self.command,
            "path": self.path,
            "token": self.headers.get("X-Admin-Token"),
            "body": json.loads(self.rfile.read(length)) if length else None,
        }
        time.sleep(_Fake.delay)
        self.send_response(_Fake.status)
        payload = b"" if _Fake.status == 204 else json.dumps(_Fake.body).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PATCH = do_DELETE = _reply

    def log_message(self, *args):
        pass


@pytest.fixture()
def server():
    _Fake.status, _Fake.body, _Fake.delay, _Fake.last = 200, {"ok": True}, 0.0, {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_get_sends_token_and_returns_json(server):
    assert api.Api(server, "tok").request("GET", "/admin/state") == {"ok": True}
    assert _Fake.last == {"method": "GET", "path": "/admin/state", "token": "tok", "body": None}


def test_post_sends_json_body(server):
    api.Api(server, "tok").request("POST", "/admin/clients", {"name": "A"})
    assert _Fake.last["body"] == {"name": "A"}


def test_204_returns_none(server):
    _Fake.status = 204
    assert api.Api(server, "tok").request("DELETE", "/admin/clients/x") is None


@pytest.mark.parametrize(
    ("status", "body", "code", "text"),
    [
        (401, {"detail": "Invalid admin token"}, 1, "token de administración no es válido"),
        (404, {"detail": "Client not found"}, 1, "Client not found"),
        (409, {"detail": "client_key already exists"}, 1, "client_key already exists"),
        (500, {"detail": "boom"}, 1, "500"),
    ],
)
def test_http_errors_map_to_exit_codes(server, status, body, code, text):
    _Fake.status, _Fake.body = status, body
    with pytest.raises(api.ApiError) as e:
        api.Api(server, "tok").request("GET", "/admin/clients")
    assert e.value.exit_code == code
    assert text in e.value.message


def test_422_is_usage_error_and_never_echoes_the_input(server):
    secret = "s" * 40
    _Fake.status = 422
    _Fake.body = {"detail": [{"loc": ["body", "client_key"], "msg": "too short", "input": secret}]}
    with pytest.raises(api.ApiError) as e:
        api.Api(server, "tok").request("POST", "/admin/clients", {"client_key": secret})
    assert e.value.exit_code == 2
    assert "client_key: too short" in e.value.message
    assert secret not in e.value.message


def test_unreachable_is_exit_3_with_hint():
    with pytest.raises(api.ApiError) as e:
        api.Api("http://127.0.0.1:9", "tok").request("GET", "/admin/state")
    assert e.value.exit_code == 3
    assert "127.0.0.1:9" in e.value.message and "jota-gw doctor" in e.value.message


def test_timeout_is_exit_3(server):
    _Fake.delay = 0.5
    with pytest.raises(api.ApiError) as e:
        api.Api(server, "tok", timeout=0.1).request("GET", "/admin/state")
    assert e.value.exit_code == 3


# --- token ---


def test_token_explicit_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_TOKEN", "env")
    assert api.resolve_token("cli") == "cli"


def test_token_falls_back_to_admin_token_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ADMIN_TOKEN", "env")
    assert api.resolve_token(None) == "env"


def test_token_from_home_config_env(monkeypatch, tmp_path):
    monkeypatch.delenv("JOTA_GATEWAY_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    (tmp_path / ".jota-gateway").mkdir()
    (tmp_path / ".jota-gateway" / "config.env").write_text(
        '# comentario\nOTRA=1\nADMIN_TOKEN="desde-fichero"\n'
    )
    assert api.resolve_token(None) == "desde-fichero"


def test_token_never_read_from_cwd_dotenv(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ADMIN_TOKEN=del-cwd\n")
    with pytest.raises(api.ApiError) as e:
        api.resolve_token(None)
    assert e.value.exit_code == 2


class _Redirect(BaseHTTPRequestHandler):
    target = ""

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", _Redirect.target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def test_redirect_is_an_error_and_never_forwards_the_token(server):
    _Redirect.target = server + "/admin/state"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Redirect)
    threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True).start()
    try:
        with pytest.raises(api.ApiError) as e:
            api.Api(f"http://127.0.0.1:{srv.server_address[1]}", "tok").request("GET", "/x")
    finally:
        srv.shutdown()
        srv.server_close()
    assert e.value.exit_code == 1
    assert "redirig" in e.value.message
    assert _Fake.last == {}  # el destino de la redirección no recibió nada


def test_non_json_2xx_is_an_error(server, monkeypatch):
    monkeypatch.setattr(json, "dumps", lambda *_a, **_k: "<html>login</html>")
    with pytest.raises(api.ApiError) as e:
        api.Api(server, "tok").request("GET", "/admin/state")
    assert e.value.exit_code == 1


# --- robustez (#306) ---


@pytest.mark.parametrize("url", ["no-es-una-url", "http://[::1"])
def test_malformed_url_is_usage_error(url):
    with pytest.raises(api.ApiError) as e:
        api.Api(url, "tok").request("GET", "/admin/state")
    assert e.value.exit_code == 2
    assert "--url" in e.value.message


class _Truncated(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "100")
        self.end_headers()
        self.wfile.write(b'{"a"')
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *args):
        pass


class _Drip(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "40")
        self.end_headers()
        for _ in range(40):
            self.wfile.write(b" ")
            self.wfile.flush()
            time.sleep(0.03)

    def log_message(self, *args):
        pass


@pytest.fixture()
def serve():
    servers = []

    def _serve(handler):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield _serve
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def test_truncated_response_is_an_error(serve):
    with pytest.raises(api.ApiError) as e:
        api.Api(serve(_Truncated), "tok").request("GET", "/admin/state")
    assert e.value.exit_code == 1


def test_total_deadline_even_if_each_read_is_fast(serve):
    started = time.monotonic()
    with pytest.raises(api.ApiError) as e:
        api.Api(serve(_Drip), "tok", timeout=0.3).request("GET", "/admin/state")
    assert e.value.exit_code == 3
    assert time.monotonic() - started < 1.0


def test_422_with_odd_detail_items_does_not_crash(server):
    _Fake.status, _Fake.body = 422, {"detail": ["raro", 3]}
    with pytest.raises(api.ApiError) as e:
        api.Api(server, "tok").request("POST", "/admin/clients", {})
    assert e.value.exit_code == 2


def test_tls_error_is_not_unreachable(server):
    with pytest.raises(api.ApiError) as e:
        api.Api(server.replace("http://", "https://"), "tok").request("GET", "/admin/state")
    assert e.value.exit_code == 1
    assert "TLS" in e.value.message


def test_unreadable_config_env_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("JOTA_GATEWAY_HOME", str(tmp_path))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    config = tmp_path / "config.env"
    config.write_text("ADMIN_TOKEN=x\n")
    config.chmod(0)
    try:
        with pytest.raises(api.ApiError) as e:
            api.resolve_token(None)
    finally:
        config.chmod(0o600)
    assert e.value.exit_code == 1
    assert str(config) in e.value.message


def test_token_from_jota_gateway_home(monkeypatch, tmp_path):
    monkeypatch.setenv("JOTA_GATEWAY_HOME", str(tmp_path))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    (tmp_path / "config.env").write_text("ADMIN_TOKEN=de-la-base\n")
    assert api.resolve_token(None) == "de-la-base"
