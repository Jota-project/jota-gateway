"""`fogar-gw access`: las vías para llegar al gateway."""

import json

import pytest
from click.testing import CliRunner

from fogar_gateway import access as access_mod
from fogar_gateway.cli import access as cli_access
from fogar_gateway.cli import app

ROUTES = [
    access_mod.Route("tailscale", "http://100.1.2.3:8004", False, "sin_verificar", "", True),
    access_mod.Route("lan", "http://192.168.1.5:8004", False),
]


@pytest.fixture()
def base(monkeypatch, tmp_path):
    monkeypatch.setenv("FOGAR_GATEWAY_HOME", str(tmp_path))
    monkeypatch.setenv("NO_COLOR", "1")
    (tmp_path / "config.env").write_text("ADMIN_TOKEN=admin-token-largo-0123456789\n")
    monkeypatch.setattr(cli_access, "gateway_instance_id", lambda url: "abc")
    monkeypatch.setattr(access_mod, "collect", lambda *a, **k: ROUTES)


def run(*args):
    return CliRunner().invoke(app.cli, list(args))


def test_access_json(base):
    r = run("--json", "access")
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["routes"][0]["kind"] == "tailscale" and data["routes"][0]["recommended"] is True
    assert isinstance(data["docker"], bool)


def test_access_gateway_down_exits_zero(base, monkeypatch):
    monkeypatch.setattr(cli_access, "gateway_instance_id", lambda url: None)
    r = run("access")
    assert r.exit_code == 0, r.output
    assert "El gateway no responde: no puedo comprobar las vías" in r.output


def test_access_http_recommended_shows_https_hint(base):
    r = run("access")
    assert r.exit_code == 0, r.output
    assert "http://100.1.2.3:8004" in r.output and "http://192.168.1.5:8004" in r.output
    assert "hace falta HTTPS" in r.output and "fogar-gw access setup" in r.output


def test_access_https_recommended_has_no_hint(base, monkeypatch):
    ok = access_mod.Route("public", "https://x.example/", True, "ok", "", True)
    monkeypatch.setattr(access_mod, "collect", lambda *a, **k: [ok])
    r = run("access")
    assert "verificada" in r.output and "hace falta HTTPS" not in r.output


@pytest.mark.parametrize("status,detail,words", [
    ("protegida", "", "protegida con Cloudflare Access: no la he podido comprobar"),
    ("inalcanzable", "timeout", "no responde (timeout)"),
    ("otro_gateway", "", "llega a otro gateway"),
    ("sin_verificar", "", "sin comprobar"),
])
def test_status_words(base, monkeypatch, status, detail, words):
    rt = access_mod.Route("lan", "http://h:1", False, status, detail)
    monkeypatch.setattr(access_mod, "collect", lambda *a, **k: [rt])
    assert words in run("access").output
