"""`jota-gw init`, `jota-gw serve` y `--home` (M14c, #295)."""

import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from jota_gateway.cli import serve as serve_mod
from jota_gateway.cli.app import cli
from jota_gateway.core.config import settings
from jota_gateway.db import database

ROOT = Path(__file__).resolve().parents[2]


def _cli(*args: str, cwd: Path, home: Path | None = None, timeout: float = 60):
    env = {k: v for k, v in os.environ.items() if k not in ("ADMIN_TOKEN", "DATABASE_URL")}
    env.pop("JOTA_GATEWAY_HOME", None)
    if home is not None:
        env["JOTA_GATEWAY_HOME"] = str(home)
    return subprocess.run([sys.executable, "-m", "jota_gateway.cli", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=timeout)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_init_creates_private_home_config_and_migrated_db(tmp_path):
    base = tmp_path / "base"
    r = _cli("--home", str(base), "--json", "init", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    db = base / "data" / "gateway.db"
    assert json.loads(r.stdout)["db"] == {"path": str(db), "state": "creada y migrada"}
    assert _mode(base) == 0o700 and _mode(base / "data") == 0o700
    assert _mode(base / "config.env") == 0o600 and _mode(db) == 0o600
    assert (base / "config.env").read_text().startswith("ADMIN_TOKEN=")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()


def test_init_never_overwrites(tmp_path):
    base = tmp_path / "base"
    r = _cli("--home", str(base), "init", cwd=tmp_path)
    assert r.returncode == 0 and "Siguiente paso" in r.stdout
    config_before = (base / "config.env").read_text()
    db_before = (base / "data" / "gateway.db").stat().st_mtime_ns
    r = _cli("--home", str(base), "--json", "init", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["config"]["state"].startswith("ya existía")
    assert out["db"]["state"].startswith("ya existía")
    assert (base / "config.env").read_text() == config_before
    assert (base / "data" / "gateway.db").stat().st_mtime_ns == db_before


def test_init_with_invalid_config_creates_no_db(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "config.env").write_text("STT_PROVIDER=roto\n")
    r = _cli("--home", str(base), "init", cwd=tmp_path)
    assert r.returncode == 1
    assert "no es válida" in r.stderr
    assert not (base / "data").exists()


def test_same_paths_from_any_working_directory(tmp_path):
    """Criterio de #295: arrancando desde dos directorios distintos, mismas rutas."""
    base = tmp_path / "base"
    probe = ("from jota_gateway import paths; from jota_gateway.core.config import settings; "
             "print(paths.config_file()); print(paths.sqlite_file(settings.DATABASE_URL))")
    seen = []
    for cwd in (tmp_path, ROOT):
        env = {**os.environ, "JOTA_GATEWAY_HOME": str(base)}
        env.pop("DATABASE_URL", None)
        r = subprocess.run([sys.executable, "-c", probe], cwd=cwd, env=env,
                           capture_output=True, text=True, check=True)
        seen.append(r.stdout)
    assert seen[0] == seen[1] == f"{base / 'config.env'}\n{base / 'data' / 'gateway.db'}\n"


def test_serve_without_db_stops_and_creates_nothing(tmp_path):
    base = tmp_path / "errata"  # p. ej. un --home mal escrito
    r = _cli("--home", str(base), "serve", cwd=tmp_path)
    assert r.returncode == 3  # uvicorn: fallo de arranque
    assert "jota-gw init" in r.stderr
    assert not base.exists()  # ni la BD, ni data/, ni el candado


def test_alembic_cli_does_not_create_a_db_either(tmp_path):
    base = tmp_path / "vacia"
    env = {**os.environ, "JOTA_GATEWAY_HOME": str(base)}
    env.pop("DATABASE_URL", None)
    ini = Path(database.__file__).resolve().parents[1] / "alembic.ini"
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", str(ini), "current"], cwd=tmp_path,
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "DatabaseMissing" in r.stderr
    assert not (base / "data" / "gateway.db").exists()


@pytest.fixture
def uvicorn_calls(monkeypatch):
    import uvicorn

    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(database, "CREATE_DB", False)
    monkeypatch.setattr(os, "umask", lambda mask: 0o022)  # no cambiar la del proceso de pytest
    return calls


def test_serve_passes_the_hardening_that_used_to_live_in_the_cmd(uvicorn_calls, monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_HOST", "127.0.0.1")
    r = CliRunner().invoke(cli, ["serve"])
    assert r.exit_code == 0, r.output
    (args, kwargs), = uvicorn_calls
    # Como cadena: uvicorn configura sus loggers antes de importar la app (core/logging.py).
    assert args == ("jota_gateway.main:app",)
    assert kwargs == {"host": "127.0.0.1", "port": 8004, "proxy_headers": False,
                      "ws_max_size": 1_048_576, "timeout_graceful_shutdown": 35}
    assert database.CREATE_DB is False


def test_serve_empty_host_listens_on_all_interfaces(uvicorn_calls, monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_HOST", "")
    assert CliRunner().invoke(cli, ["serve"]).exit_code == 0
    assert uvicorn_calls[0][1]["host"] == "0.0.0.0"


def test_serve_create_db_allows_creating_it(uvicorn_calls):
    assert CliRunner().invoke(cli, ["serve", "--create-db"]).exit_code == 0
    assert database.CREATE_DB is True


def test_docker_image_starts_with_serve_create_db():
    cmd = (ROOT / "Dockerfile").read_text(encoding="utf-8").rsplit("\nCMD", 1)[1]
    assert json.loads(cmd.strip()) == ["jota-gw", "serve", "--create-db"]


def test_serve_constants_match_the_shutdown_budget():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "stop_grace_period: 45s" in compose
    assert serve_mod.GRACEFUL_SHUTDOWN_S < 45


def test_serve_invalid_config_exits_3_with_field_and_no_value(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "config.env").write_text("OPENCLAW_PORT=valor-secreto-abc\n")
    r = _cli("--home", str(base), "serve", cwd=tmp_path)
    assert r.returncode == 3
    assert "OPENCLAW_PORT" in r.stderr and "valor-secreto-abc" not in r.stderr + r.stdout
    assert "Traceback" not in r.stderr
