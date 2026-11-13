"""`fogar-gw migrate --from-docker` (M14e, #297)."""

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from fogar_gateway.cli import migrate
from fogar_gateway.cli.app import cli

ROOT = Path(__file__).resolve().parents[2]
ENV = (
    "# Docker\n"
    "export ADMIN_TOKEN='tok en'\n"
    "\n"
    'OPENCLAW_HOST="host:1"\n'
    "DATABASE_URL=sqlite:////app/data/gateway.db\n"
    "GATEWAY_UID=1000\n"
    "  gateway_gid = 1000\n"
    "HANDSHAKE_MAX_BYTES=4096\n"
)


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """Un directorio Docker falso: BD hecha con `init` en otra base y un `.env` a mano."""
    base = tmp_path_factory.mktemp("docker-template")
    env = {k: v for k, v in os.environ.items() if k not in ("ADMIN_TOKEN", "DATABASE_URL")}
    r = subprocess.run([sys.executable, "-m", "fogar_gateway.cli", "--home", str(base), "init"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (base / "config.env").unlink()
    (base / ".env").write_text(ENV)
    from sqlmodel import Session, create_engine

    from fogar_gateway.db.models import ClientRecord

    engine = create_engine(f"sqlite:///{base / 'data' / 'gateway.db'}")
    with Session(engine) as session:
        session.add_all(ClientRecord(id=i, name=i, client_key=f"k-{i}") for i in ("b-id", "a-id"))
        session.commit()
    engine.dispose()
    return base


@pytest.fixture()
def env(template, tmp_path, monkeypatch):
    docker = tmp_path / "docker"
    shutil.copytree(template, docker)
    (docker / "data" / "gateway.db.bak-pre-0001").write_text("copia")
    native = tmp_path / "native"
    monkeypatch.setenv("FOGAR_GATEWAY_HOME", str(native))
    for k in ("HANDSHAKE_MAX_BYTES", "ADMIN_TOKEN", "DATABASE_URL", "LOG_FORMAT"):
        monkeypatch.delenv(k, raising=False)
    return docker, native


def run(docker, *extra):
    return CliRunner().invoke(cli, ["--json", "migrate", "--from-docker", str(docker), *extra])


def snapshot(root: Path):
    return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def ids(db: Path):
    with sqlite3.connect(db) as con:
        return [r[0] for r in con.execute("SELECT id FROM clients ORDER BY id")]


def test_copies_db_and_config_600(env):
    docker, native = env
    r = run(docker)
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["db"]["clients"] == 2 and out["db"]["path"] == str(native / "data" / "gateway.db")
    assert out["config"]["path"] == str(native / "config.env")
    assert mode(native) == 0o700 and mode(native / "data") == 0o700
    assert mode(native / "config.env") == 0o600 and mode(native / "data" / "gateway.db") == 0o600
    assert not list(native.rglob("*.tmp"))


def test_same_client_ids(env):
    docker, native = env
    assert run(docker).exit_code == 0
    assert ids(native / "data" / "gateway.db") == ids(docker / "data" / "gateway.db") == ["a-id", "b-id"]


def test_drops_database_url_and_uid_gid(env):
    docker, native = env
    out = json.loads(run(docker).output)
    assert out["config"]["dropped"] == ["DATABASE_URL", "GATEWAY_UID", "gateway_gid"]
    text = (native / "config.env").read_text()
    assert "DATABASE_URL" not in text and "UID" not in text and "gid" not in text
    # el resto se conserva tal cual: export, comentario, comillas, línea vacía
    assert text.startswith("# Docker\nexport ADMIN_TOKEN='tok en'\n\nOPENCLAW_HOST=\"host:1\"\n")


def test_adds_log_format_json(env):
    docker, native = env
    out = json.loads(run(docker).output)
    assert out["config"]["added"] == ["LOG_FORMAT=json"]
    assert (native / "config.env").read_text().endswith("HANDSHAKE_MAX_BYTES=4096\nLOG_FORMAT=json\n")


def test_keeps_existing_log_format(env):
    docker, native = env
    with open(docker / ".env", "a") as f:
        f.write("LOG_FORMAT=text")  # sin salto final
    out = json.loads(run(docker).output)
    assert out["config"]["added"] == []
    assert (native / "config.env").read_text().count("LOG_FORMAT") == 1


def test_refuses_existing_target(env):
    docker, native = env
    (native / "data").mkdir(parents=True)
    (native / "config.env").write_text("X=1\n")
    before = snapshot(native)
    r = run(docker)
    assert r.exit_code == 1 and "no se sobrescribe" in r.output
    assert snapshot(native) == before


def test_refuses_existing_db_only(env):
    docker, native = env
    (native / "data").mkdir(parents=True)
    (native / "data" / "gateway.db").write_text("x")
    assert run(docker).exit_code == 1
    assert not (native / "config.env").exists()


def test_refuses_missing_source(env, tmp_path):
    _, native = env
    r = run(tmp_path / "nada")
    assert r.exit_code == 1 and not native.exists()


def test_refuses_revision_mismatch(env):
    docker, native = env
    with sqlite3.connect(docker / "data" / "gateway.db") as con:
        con.execute("UPDATE alembic_version SET version_num = 'viejaXYZ'")
    r = run(docker)
    assert r.exit_code == 1 and "viejaXYZ" in r.output and "Despliega la misma versión" in r.output
    assert not native.exists()


class _FailingBackup:
    """Conexión de origen cuyo backup falla (no se puede parchear `sqlite3.Connection`)."""

    def __init__(self, con):
        self._con = con

    def backup(self, target, **kw):
        raise sqlite3.OperationalError("disco lleno")

    def __getattr__(self, name):
        return getattr(self._con, name)


def test_failure_leaves_no_partial_files(env, monkeypatch):
    docker, native = env
    monkeypatch.setattr(migrate, "_ro", lambda p: _FailingBackup(sqlite3.connect(p)))
    r = run(docker)
    assert r.exit_code == 1 and isinstance(r.exception, SystemExit)
    assert "OperationalError" in r.output and "No se ha creado nada" in r.output
    assert not native.exists()


def test_failure_on_existing_base_keeps_it_clean(env, monkeypatch):
    docker, native = env
    native.mkdir()
    monkeypatch.setattr(migrate, "_ro", lambda p: _FailingBackup(sqlite3.connect(p)))
    assert run(docker).exit_code == 1
    assert list(native.iterdir()) == []


def test_failure_after_db_copy_removes_the_db(env, monkeypatch):
    docker, native = env
    real = os.replace

    def replace(src, dst):
        if str(dst).endswith("config.env"):
            raise OSError("fallo")
        real(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    assert run(docker).exit_code == 1
    assert not native.exists()


def test_invalid_config_removes_both_files(env):
    docker, native = env
    with open(docker / ".env", "a") as f:
        f.write("HANDSHAKE_MAX_BYTES=no-es-un-entero\n")
    r = run(docker)
    assert r.exit_code == 1 and "HANDSHAKE_MAX_BYTES" in r.output
    assert not native.exists()


def test_shell_env_does_not_hide_bad_env(env, monkeypatch):
    docker, native = env
    with open(docker / ".env", "a") as f:
        f.write("HANDSHAKE_MAX_BYTES=no-es-un-entero\n")
    monkeypatch.setenv("HANDSHAKE_MAX_BYTES", "4096")  # la shell tapa el error si no se quita
    assert run(docker).exit_code == 1
    assert not native.exists()
    assert os.environ["HANDSHAKE_MAX_BYTES"] == "4096"


def test_source_untouched(env):
    docker, _ = env
    before = snapshot(docker)
    assert run(docker).exit_code == 0
    assert snapshot(docker) == before


def test_does_not_copy_bak_files(env):
    docker, native = env
    assert run(docker).exit_code == 0
    assert sorted(p.name for p in (native / "data").iterdir()) == ["gateway.db"]


def _set_env(docker, text):
    (docker / ".env").write_text(text)


@pytest.mark.parametrize("url", ["sqlite:////app/data/gateway.db", "sqlite:///data/gateway.db",
                                 "sqlite:///./data/gateway.db"])
def test_accepts_database_url_pointing_to_the_copied_file(env, url):
    docker, native = env
    _set_env(docker, f"DATABASE_URL={url}\n")
    assert run(docker).exit_code == 0, url


@pytest.mark.parametrize("url", ["sqlite:////otra/ruta.db", "sqlite:///otra.db",
                                 "postgresql://u:p@h/db"])
def test_refuses_database_url_pointing_elsewhere(env, url):
    docker, native = env
    _set_env(docker, f"DATABASE_URL={url}\n")
    r = run(docker)
    assert r.exit_code == 1 and "DATABASE_URL" in r.output
    assert not native.exists()


def test_refuses_running_docker_gateway(env):
    import fcntl

    docker, native = env
    lock = docker / "data" / "gateway.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        r = run(docker)
    finally:
        os.close(fd)
    assert r.exit_code == 1 and "docker compose stop" in r.output
    assert not native.exists()
    assert run(docker).exit_code == 0  # libre: migra


def test_does_not_create_lock_in_source(env):
    docker, _ = env
    assert not (docker / "data" / "gateway.lock").exists()
    assert run(docker).exit_code == 0
    assert not (docker / "data" / "gateway.lock").exists()


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_refuses_sqlite_leftovers_in_target(env, suffix):
    docker, native = env
    (native / "data").mkdir(parents=True)
    (native / "data" / f"gateway.db{suffix}").write_text("resto")
    r = run(docker)
    assert r.exit_code == 1 and "no se sobrescribe" in r.output
    assert not (native / "config.env").exists()


def test_refuses_when_head_unknown(env, monkeypatch):
    docker, native = env
    monkeypatch.setattr(migrate.doctor, "revisions", lambda: (set(), None))
    assert run(docker).exit_code == 1 and not native.exists()


def test_refuses_alembic_version_with_two_rows(env):
    docker, native = env
    with sqlite3.connect(docker / "data" / "gateway.db") as con:
        con.execute("DROP TABLE alembic_version")
        con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        con.execute("INSERT INTO alembic_version VALUES ('a'), ('b')")
    r = run(docker)
    assert r.exit_code == 1 and "exactamente una fila" in r.output and not native.exists()


def test_umask_restored(env):
    docker, _ = env
    old = os.umask(0o022)
    try:
        assert run(docker).exit_code == 0
        assert os.umask(old) == 0o022
    finally:
        os.umask(old)


@pytest.mark.skipif(os.geteuid() == 0, reason="root lee cualquier fichero")
def test_unreadable_lock_is_a_clean_error(env):
    docker, native = env
    lock = docker / "data" / "gateway.lock"
    lock.write_text("")
    lock.chmod(0)
    try:
        r = run(docker)
    finally:
        lock.chmod(0o600)
    assert r.exit_code == 1 and isinstance(r.exception, SystemExit)
    assert "PermissionError" in r.output and "gateway.lock" in r.output
    assert not native.exists()


def test_non_utf8_env_is_a_clean_error(env):
    docker, native = env
    (docker / ".env").write_bytes("OPENCLAW_TOKEN=caf\xe9\n".encode("latin-1"))
    r = run(docker)
    assert r.exit_code == 1 and isinstance(r.exception, SystemExit)
    assert "UTF-8" in r.output
    assert not native.exists()


# --- identidad ante OpenClaw (revisión M19) ---

DEVICE = "openclaw-device.json"


def _with_device(docker: Path) -> bytes:
    from fogar_gateway.services.openclaw.device import DeviceStore

    store = DeviceStore(docker / "data" / DEVICE)
    store.load_or_create()
    store.save_token("host:1", "devtok-docker", ["operator.read", "operator.admin"])
    (docker / "data" / f".{DEVICE}.lock").unlink()  # un Docker real no tiene por qué tenerlo
    (docker / "data" / DEVICE).chmod(0o644)  # aunque en origen sea más abierto
    return (docker / "data" / DEVICE).read_bytes()


def test_copies_the_openclaw_identity_600(env):
    docker, native = env
    original = _with_device(docker)
    before = snapshot(docker)
    r = run(docker)
    assert r.exit_code == 0, r.output
    copied = native / "data" / DEVICE
    assert copied.read_bytes() == original and mode(copied) == 0o600
    assert json.loads(r.output)["openclaw_device"] == str(copied)
    assert snapshot(docker) == before  # origen intacto
    assert not list(native.rglob("*.tmp"))
    assert "devtok-docker" not in r.output


def test_without_identity_nothing_is_copied(env):
    docker, native = env
    r = run(docker)
    assert r.exit_code == 0
    assert json.loads(r.output)["openclaw_device"] is None
    assert not (native / "data" / DEVICE).exists()


def test_refuses_existing_identity_in_target(env):
    docker, native = env
    _with_device(docker)
    (native / "data").mkdir(parents=True)
    (native / "data" / DEVICE).write_text("otra")
    r = run(docker)
    assert r.exit_code == 1 and DEVICE in r.output
    assert (native / "data" / DEVICE).read_text() == "otra"
    assert not (native / "config.env").exists()


def test_failed_identity_copy_leaves_no_partial_files(env, monkeypatch):
    docker, native = env
    _with_device(docker)
    real = os.replace

    def replace(src, dst):
        if str(dst).endswith(DEVICE):
            raise OSError("fallo")
        real(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    r = run(docker)
    assert r.exit_code == 1 and "No se ha creado nada" in r.output
    assert not native.exists()
