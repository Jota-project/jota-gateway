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
