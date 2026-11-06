"""`jota-gw doctor` (M14b, #294): una prueba por cada fallo que debe explicar."""

import json
import socket
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from click.testing import CliRunner

from jota_gateway.cli import doctor
from jota_gateway.cli.app import cli

SECRET = "s3cr3t-admin-token-0123456789"
ROOT = Path(__file__).resolve().parents[2]


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _head() -> str:
    from alembic.script import ScriptDirectory

    head = ScriptDirectory(str(doctor.MIGRATIONS.parent)).get_current_head()
    assert head
    return head


def _make_db(path: Path, revision: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    if revision is not None:
        con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        con.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    con.commit()
    con.close()
    path.chmod(0o600)
    path.parent.chmod(0o700)


@pytest.fixture()
def base(monkeypatch, tmp_path):
    home = tmp_path / "base"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("JOTA_GATEWAY_HOME", str(home))
    for name in ("ADMIN_TOKEN", "JOTA_GW_TOKEN", "DATABASE_URL", "OPENCLAW_HOST", "OPENCLAW_PORT",
                 "OPENCLAW_TOKEN", "TRANSCRIBER_WS_URL", "TTS_WS_URL", "TTS_TOKEN",
                 "STT_PROVIDER", "TTS_PROVIDER", "JOTA_GW_URL"):
        monkeypatch.delenv(name, raising=False)
    closed = _closed_port()
    config = home / "config.env"
    config.write_text(
        f"ADMIN_TOKEN={SECRET}\nOPENCLAW_TOKEN=otro-secreto-largo\nTTS_TOKEN=tts-propio\n"
        f"OPENCLAW_PORT={closed}\nTRANSCRIBER_WS_URL=127.0.0.1:{closed}\n"
        f"TTS_WS_URL=127.0.0.1:{closed}\n"
    )
    config.chmod(0o600)
    _make_db(home / "data" / "gateway.db", _head())
    return home


def run(*args):
    return CliRunner().invoke(cli, ["--url", f"http://127.0.0.1:{_closed_port()}", *args])


def checks(result) -> dict[str, dict]:
    return {c["check"]: c for c in json.loads(result.output)}


def test_healthy_offline_install_has_no_failures_except_services(base):
    r = run("--json", "doctor")
    found = checks(r)
    assert found["configuración"]["level"] == "ok"
    assert found["base de datos"]["level"] == "ok"
    assert found["gateway"]["level"] == "warn"
    assert found["orquestador"]["level"] == "fail"  # el puerto configurado está cerrado
    assert r.exit_code == 1


def test_paths_are_reported(base):
    found = checks(run("--json", "doctor"))
    assert str(base / "config.env") in found["rutas"]["detail"]
    assert str(base / "data" / "gateway.db") in found["rutas"]["detail"]


def test_invalid_config_is_a_failure_naming_the_field_only(base):
    (base / "config.env").write_text(f"ADMIN_TOKEN={SECRET}\nSTT_PROVIDER=valor-raro-xyz\n")
    r = run("--json", "doctor")
    found = checks(r)
    assert found["configuración"]["level"] == "fail"
    assert "STT_PROVIDER" in found["configuración"]["detail"]
    assert "valor-raro-xyz" not in r.output and r.exit_code == 1


def test_empty_admin_token_is_a_failure(base):
    (base / "config.env").write_text("OPENCLAW_TOKEN=otro-secreto-largo\n")
    assert checks(run("--json", "doctor"))["ADMIN_TOKEN"]["level"] == "fail"


def test_missing_db_is_a_failure_and_doctor_never_creates_it(base):
    db = base / "data" / "gateway.db"
    db.unlink()
    (base / "data").rmdir()
    found = checks(run("--json", "doctor"))
    assert found["base de datos"]["level"] == "fail"
    assert str(db) in found["base de datos"]["detail"]
    assert not (base / "data").exists()


INITIAL = "21cb0cf4f6f9"  # src/jota_gateway/migrations/versions/21cb0cf4f6f9_initial_schema.py


def test_pending_migrations_is_a_warning(base):
    _make_db(base / "data" / "old.db", INITIAL)
    with open(base / "config.env", "a") as f:
        f.write("DATABASE_URL=sqlite:///data/old.db\n")
    found = checks(run("--json", "doctor"))
    assert found["base de datos"]["level"] == "warn"
    assert INITIAL in found["base de datos"]["detail"]


def test_unknown_migration_is_a_failure(base):
    _make_db(base / "data" / "new.db", "ffff_del_futuro")
    with open(base / "config.env", "a") as f:
        f.write("DATABASE_URL=sqlite:///data/new.db\n")
    found = checks(run("--json", "doctor"))
    assert found["base de datos"]["level"] == "fail"
    assert "ffff_del_futuro" in found["base de datos"]["detail"]


def test_db_without_alembic_version_is_a_failure(base):
    _make_db(base / "data" / "raw.db", None)
    with open(base / "config.env", "a") as f:
        f.write("DATABASE_URL=sqlite:///data/raw.db\n")
    assert checks(run("--json", "doctor"))["base de datos"]["level"] == "fail"


def test_doctor_does_not_write_the_db(base):
    db = base / "data" / "gateway.db"
    before = db.stat().st_mtime_ns, db.read_bytes()
    run("--json", "doctor")
    assert (db.stat().st_mtime_ns, db.read_bytes()) == before


def test_loose_permissions_are_warnings_with_the_fix(base):
    (base / "config.env").chmod(0o644)
    (base / "data" / "gateway.db").chmod(0o644)
    (base / "data").chmod(0o755)
    found = checks(run("--json", "doctor"))
    assert found["permisos"]["level"] == "warn"
    for fix in ("chmod 600", "chmod 700"):
        assert fix in found["permisos"]["detail"]


@pytest.mark.skipif(not Path("/proc/locks").exists(), reason="/proc/locks solo existe en Linux")
def test_lock_held_by_another_process_is_reported(base):
    import fcntl

    lock = base / "data" / "gateway.lock"
    with open(lock, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        found = checks(run("--json", "doctor"))
    assert "en marcha" in found["candado"]["detail"]
    assert "revisa --url" in found["gateway"]["detail"]


@pytest.mark.skipif(Path("/proc/locks").exists(), reason="fuera de Linux no se comprueba")
def test_lock_not_checked_outside_linux(base):
    assert "no comprobado" in checks(run("--json", "doctor"))["candado"]["detail"]


# --- con el gateway en marcha (servidor falso) ---


class _Gateway(BaseHTTPRequestHandler):
    routes: ClassVar[dict] = {}

    def do_GET(self):
        status, body = self.routes.get(self.path, (404, {"detail": "Not Found"}))
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture()
def gateway():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Gateway)
    threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True).start()
    _Gateway.routes = {
        "/healthz": (200, {"status": "ok"}),
        "/admin/state": (200, {"gateway": {"version": "1.23.0"}}),
        "/ready": (200, {"status": "ok", "services": {
            "orchestrator": "ok", "transcriber": "ok", "tts": "ok"}}),
    }
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def run_at(url, *args):
    return CliRunner().invoke(cli, ["--url", url, *args])


def test_everything_ok_exits_0(base, gateway):
    r = run_at(gateway, "--json", "doctor")
    found = checks(r)
    assert r.exit_code == 0, r.output
    for name in ("gateway", "token", "orquestador", "STT", "TTS"):
        assert found[name]["level"] == "ok", found[name]


def test_wrong_token_is_a_failure(base, gateway):
    _Gateway.routes["/admin/state"] = (401, {"detail": "Invalid admin token"})
    r = run_at(gateway, "--json", "doctor")
    assert checks(r)["token"]["level"] == "fail" and r.exit_code == 1


def test_orchestrator_down_through_ready_is_a_failure(base, gateway):
    _Gateway.routes["/ready"] = (503, {"status": "unavailable", "services": {
        "orchestrator": "unavailable", "transcriber": "ok", "tts": "unavailable"}})
    found = checks(run_at(gateway, "--json", "doctor"))
    assert found["orquestador"]["level"] == "fail"
    assert found["STT"]["level"] == "ok"
    assert found["TTS"]["level"] == "warn"


VOICE_OFF = "voz desactivada: configúrala con `jota-gw onboard`"


@pytest.mark.parametrize("online", [False, True])
def test_voice_off_is_info_not_a_failure(base, gateway, online):
    with open(base / "config.env", "a") as f:
        f.write("STT_PROVIDER=off\nTTS_PROVIDER=off\n")
    # En marcha, /ready da los proveedores `off` como no disponibles (probe() es False).
    _Gateway.routes["/ready"] = (200, {"status": "degraded", "services": {
        "orchestrator": "ok", "transcriber": "unavailable", "tts": "unavailable"}})
    r = run_at(gateway, "--json", "doctor") if online else run("--json", "doctor")
    found = checks(r)
    for name in ("STT", "TTS"):
        assert found[name] == {"level": "info", "check": name, "detail": VOICE_OFF}
    assert found["configuración"]["level"] == "ok"


def test_no_secret_in_any_output(base, gateway):
    for args in (("--json", "doctor"), ("doctor",)):
        assert SECRET not in run_at(gateway, *args).output


def test_plain_output_marks_each_level(base):
    r = run("doctor")
    assert "✗" in r.output and "✓" in r.output and "!" in r.output


def test_doctor_module_import_is_light():
    import subprocess

    code = ("import sys, jota_gateway.cli.doctor; "
            "print('HEAVY=' + ','.join(m for m in ('pydantic','sqlmodel','alembic','rich') "
            "if m in sys.modules))")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                       check=True)
    assert r.stdout.strip() == "HEAVY="


def test_module_exposes_run_checks():
    assert callable(doctor.run_checks)


def test_missing_config_env_is_said_plainly(base):
    (base / "config.env").unlink()
    found = checks(run("--json", "doctor"))
    assert found["configuración"]["level"] == "warn"
    assert "No existe" in found["configuración"]["detail"]


def test_unreachable_gateway_message_does_not_recommend_doctor(base):
    assert "jota-gw doctor" not in checks(run("--json", "doctor"))["gateway"]["detail"]


# --- revisión de 14b ---


@pytest.mark.parametrize("where", ["file", "env"])
def test_malformed_json_setting_is_a_clean_failure(base, monkeypatch, where):
    if where == "env":
        monkeypatch.setenv("TTS_VOICE_MAP", "notjson")
    else:
        with open(base / "config.env", "a") as f:
            f.write("TTS_VOICE_MAP=notjson\n")
    r = run("--json", "doctor")
    found = checks(r)  # JSON válido: sin traceback
    assert found["configuración"]["level"] == "fail"
    assert "TTS_VOICE_MAP" in found["configuración"]["detail"]
    assert "notjson" not in r.output


def test_unreadable_config_env_is_a_check_and_doctor_goes_on(base):
    (base / "config.env").chmod(0)
    try:
        found = checks(run("--json", "doctor"))
    finally:
        (base / "config.env").chmod(0o600)
    assert found["configuración"]["level"] == "fail"
    assert "gateway" in found


def test_db_in_the_working_directory_is_ignored(base, monkeypatch, tmp_path):
    """Como el gateway desde M14c: una ruta relativa cuelga de la base, nunca del cwd."""
    (base / "data" / "gateway.db").unlink()
    cwd = tmp_path / "repo"
    _make_db(cwd / "data" / "gateway.db", _head())
    monkeypatch.chdir(cwd)
    found = checks(run("--json", "doctor"))
    assert found["base de datos"]["level"] == "fail"
    assert str(base / "data" / "gateway.db") in found["base de datos"]["detail"]
    assert "jota-gw init" in found["base de datos"]["detail"]


def test_paths_not_owned_by_the_user_are_not_flagged(base, monkeypatch):
    base.chmod(0o755)
    real = doctor.os.getuid()
    monkeypatch.setattr(doctor.os, "getuid", lambda: real + 1)
    assert checks(run("--json", "doctor"))["permisos"]["level"] != "warn"


def test_nothing_to_check_is_not_ok(base):
    import shutil

    shutil.rmtree(base)
    assert checks(run("--json", "doctor"))["permisos"]["level"] == "info"


def test_missing_migrations_dir_is_not_checked(base, monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "MIGRATIONS", tmp_path / "no-existe")
    found = checks(run("--json", "doctor"))
    assert found["base de datos"]["level"] == "info"
    assert "no comprobado" in found["base de datos"]["detail"]


def test_db_path_with_uri_characters(monkeypatch, tmp_path):
    home = tmp_path / "a#b?c%d"
    home.mkdir()
    monkeypatch.setenv("JOTA_GATEWAY_HOME", str(home))
    _make_db(home / "data" / "gateway.db", _head())
    check = doctor._check_db(home / "data" / "gateway.db", "sqlite:///data/gateway.db")
    assert check["level"] == "ok", check
