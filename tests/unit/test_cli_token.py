"""`fogar-gw token`: el token del dashboard, solo en una terminal."""

import pytest
from click.testing import CliRunner

from fogar_gateway.cli import app, output

ADMIN = "admin-token-largo-0123456789"


@pytest.fixture()
def base(monkeypatch, tmp_path):
    monkeypatch.setenv("FOGAR_GATEWAY_HOME", str(tmp_path))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")  # texto plano: la salida de rich no la captura CliRunner
    (tmp_path / "config.env").write_text(f"ADMIN_TOKEN={ADMIN}\n")
    return tmp_path


def run(*args):
    return CliRunner().invoke(app.cli, list(args))


@pytest.mark.parametrize("args", [["token"], ["--json", "token"]])
def test_without_a_terminal_never_writes_the_token(base, args):
    r = run(*args)  # el stdout de CliRunner no es una terminal
    assert r.exit_code == 1
    assert ADMIN not in r.output
    assert f"{base / 'config.env'} (ADMIN_TOKEN)" in r.output


def test_on_a_terminal_writes_the_token_and_the_warning(base, monkeypatch):
    monkeypatch.setattr(output, "_isatty", lambda: True)
    r = run("token")
    assert r.exit_code == 0, r.output
    assert r.output.splitlines()[0] == ADMIN  # sola en su línea, para copiarla entera
    assert "No lo compartas" in r.output


def test_empty_token_says_how_to_fix_it(base, monkeypatch):
    monkeypatch.setattr(output, "_isatty", lambda: True)
    (base / "config.env").write_text("ADMIN_TOKEN=\n")
    r = run("token")
    assert r.exit_code == 1 and "ADMIN_TOKEN está vacío" in r.output
