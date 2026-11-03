"""`jota-gw config show | get | validate` (M14b, #294)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from jota_gateway.cli.app import cli

ROOT = Path(__file__).resolve().parents[2]
SECRET = "s3cr3t-admin-token-0123456789"


@pytest.fixture()
def home(monkeypatch, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setenv("JOTA_GATEWAY_HOME", str(base))
    for name in ("ADMIN_TOKEN", "OPENCLAW_TOKEN", "TTS_WS_URL", "STT_PROVIDER", "LOG_FORMAT"):
        monkeypatch.delenv(name, raising=False)
    (base / "config.env").write_text(
        f"ADMIN_TOKEN={SECRET}\nOPENCLAW_TOKEN=otro-secreto-largo\nLOG_FORMAT=json\n"
    )
    return base


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_show_gives_origin_and_masks_secrets(home, monkeypatch):
    monkeypatch.setenv("TTS_WS_URL", "tts.local:8005")
    r = run("config", "show")
    assert r.exit_code == 0, r.output
    lines = {line.split()[0]: line for line in r.output.splitlines() if line.strip()}
    assert "json" in lines["LOG_FORMAT"] and "config.env" in lines["LOG_FORMAT"]
    assert "tts.local:8005" in lines["TTS_WS_URL"] and "entorno" in lines["TTS_WS_URL"]
    assert "por defecto" in lines["TURN_TIMEOUT_S"]
    assert "definido" in lines["ADMIN_TOKEN"]
    assert "vacío" in lines["TRANSCRIBER_TOKEN"]
    assert SECRET not in r.output and "otro-secreto-largo" not in r.output


def test_show_json_masks_secrets(home):
    r = run("--json", "config", "show")
    data = json.loads(r.output)
    assert data["LOG_FORMAT"] == {"value": "json", "origin": str(home / "config.env")}
    assert data["ADMIN_TOKEN"]["value"] == "•••• (definido)"
    assert SECRET not in r.output


def test_get_is_case_insensitive(home):
    r = run("config", "get", "log_format")
    assert r.exit_code == 0
    assert "json" in r.output


def test_get_secret_is_masked(home):
    r = run("--json", "config", "get", "ADMIN_TOKEN")
    assert SECRET not in r.output
    assert json.loads(r.output)["value"] == "•••• (definido)"


def test_get_unknown_suggests_and_exits_2(home):
    r = run("config", "get", "ADMIN_TOKN")
    assert r.exit_code == 2
    assert "ADMIN_TOKEN" in r.output


def test_validate_ok(home):
    r = run("config", "validate")
    assert r.exit_code == 0, r.output


def test_validate_invalid_value_names_field_not_value(home):
    (home / "config.env").write_text(f"ADMIN_TOKEN={SECRET}\nSTT_PROVIDER=valor-raro-xyz\n")
    r = run("config", "validate")
    assert r.exit_code == 1
    assert "STT_PROVIDER" in r.output and "valor-raro-xyz" not in r.output


def test_validate_empty_admin_token_fails(home):
    (home / "config.env").write_text("OPENCLAW_TOKEN=otro-secreto-largo\n")
    r = run("config", "validate")
    assert r.exit_code == 1
    assert "ADMIN_TOKEN" in r.output


def test_missing_config_env_still_works_with_defaults(home):
    (home / "config.env").unlink()
    r = run("config", "get", "LOG_FORMAT")
    assert r.exit_code == 0
    assert "por defecto" in r.output


def test_cwd_dotenv_is_never_read_even_if_broken(tmp_path):
    # Importar jota_gateway.core.config construye un Settings() global con el .env del directorio actual.
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".env").write_text("ADMIN_TOKEN=del-cwd\nSTT_PROVIDER=roto\n")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "JOTA_GATEWAY_HOME": str(tmp_path / "vacia")}
    env.pop("ADMIN_TOKEN", None)
    env.pop("STT_PROVIDER", None)
    r = subprocess.run(
        [sys.executable, "-m", "jota_gateway.cli", "--json", "config", "get", "ADMIN_TOKEN"],
        cwd=cwd, env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["value"] == "(vacío)"


def test_get_secret_plain_is_masked(home):
    r = run("config", "get", "ADMIN_TOKEN")
    assert r.exit_code == 0
    assert SECRET not in r.output and "definido" in r.output


def test_database_url_password_is_masked(home, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw-secreta-123@db.local/gw")
    for args in (("config", "show"), ("--json", "config", "get", "DATABASE_URL")):
        r = run(*args)
        assert "pw-secreta-123" not in r.output
        assert "user:" in r.output and "db.local" in r.output
