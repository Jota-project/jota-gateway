"""Servicio de usuario de systemd: unidad y elección del ejecutable (M14d, #296)."""

import json
import sys
from pathlib import Path

import pytest

from jota_gateway.cli import api, service
from jota_gateway.cli import serve as serve_mod

UNIT = """[Unit]
Description=Jota gateway
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=exec
ExecStart=/home/u/.local/bin/jota-gw serve
Environment=JOTA_GATEWAY_HOME=/home/u/.jota-gateway
Restart=on-failure
RestartSec=5
RestartPreventExitStatus=3
TimeoutStopSec=45
UMask=0077

[Install]
WantedBy=default.target
"""


def test_unit_text_exact():
    text = service.render_unit(Path("/home/u/.local/bin/jota-gw"), Path("/home/u/.jota-gateway"))
    assert text == UNIT
    assert "--create-db" not in text and "LOG_FORMAT" not in text


def test_stop_timeout_matches_compose_and_exceeds_graceful_shutdown():
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text()
    assert "stop_grace_period: 45s" in compose
    assert "TimeoutStopSec=45" in UNIT
    assert serve_mod.GRACEFUL_SHUTDOWN_S < 45


def test_unit_path_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert service.unit_path() == tmp_path / "systemd/user/jota-gateway.service"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert service.unit_path() == tmp_path / ".config/systemd/user/jota-gateway.service"


def _tool_env(base: Path, *, editable: bool | None = None) -> Path:
    """Crea `<base>/bin/jota-gw` con su site-packages; devuelve el ejecutable."""
    exe = base / "bin" / "jota-gw"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    if editable is not None:
        dist = base / "lib/python3.12/site-packages/jota_gateway-0.1.dist-info"
        dist.mkdir(parents=True)
        (dist / "direct_url.json").write_text(
            json.dumps({"url": "file:///x", "dir_info": {"editable": editable}})
        )
    return exe


def test_pick_exe_accepts_uv_tool_and_keeps_symlink(monkeypatch, tmp_path):
    real = _tool_env(tmp_path / "tools/jota-gateway", editable=False)
    link = tmp_path / "local/bin/jota-gw"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    monkeypatch.setattr(service.shutil, "which", lambda name: str(link))
    assert service.pick_exe(None) == link  # sin resolver: sobrevive a `uv tool upgrade`


@pytest.mark.parametrize("kind", ["pyproject", "editable"])
def test_exe_inside_checkout_is_rejected(monkeypatch, tmp_path, kind):
    if kind == "pyproject":
        (tmp_path / "clone").mkdir()
        (tmp_path / "clone/pyproject.toml").write_text("")
        exe = _tool_env(tmp_path / "clone/.venv")
    else:
        exe = _tool_env(tmp_path / "venv", editable=True)
    monkeypatch.setattr(service.shutil, "which", lambda name: str(exe))
    with pytest.raises(api.ApiError, match="--exe") as e:
        service.pick_exe(None)
    assert e.value.exit_code == 1
    assert service.pick_exe(exe) == exe  # explícito: se acepta


def test_pick_exe_without_jota_gw_fails(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    with pytest.raises(api.ApiError, match="--exe") as e:
        service.pick_exe(None)
    assert e.value.exit_code == 1


def test_require_systemd_off_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(api.ApiError, match="docker compose") as e:
        service.require_systemd()
    assert e.value.exit_code == 1


def test_require_systemd_without_systemctl(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    with pytest.raises(api.ApiError, match="docker compose"):
        service.require_systemd()
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/systemctl")
    service.require_systemd()


def test_systemctl_runs_user_instance(monkeypatch):
    import subprocess

    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    service.systemctl("start", "jota-gateway.service", check=False)
    cmd, kw = calls[0]
    assert cmd == ["systemctl", "--user", "start", "jota-gateway.service"]
    assert kw["check"] is False and kw["text"] is True and kw["capture_output"] is True


def test_pick_exe_ignores_distant_pyproject(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text("")  # suelto en un ancestro lejano (~)
    exe = _tool_env(tmp_path / ".local/share/uv/tools/jota-gateway", editable=False)
    monkeypatch.setattr(service.shutil, "which", lambda name: str(exe))
    assert service.pick_exe(None) == exe


def test_importing_service_does_not_load_subprocess():
    import subprocess

    code = "import sys; import jota_gateway.cli.service; print('subprocess' in sys.modules)"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "False"


# --- install / uninstall / start / stop / restart (M14d, B2 y B3) ---

import socket  # noqa: E402
import subprocess  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from click.testing import CliRunner  # noqa: E402

from jota_gateway.cli import config as cfg  # noqa: E402
from jota_gateway.cli import output  # noqa: E402
from jota_gateway.cli.app import cli  # noqa: E402

EXE = "/home/u/.local/bin/jota-gw"
REAL_PORT_FREE = getattr(service, "port_free", None)  # antes de que la fixture lo mockee


@pytest.fixture()
def svc(monkeypatch, tmp_path):
    """Todo mockeado: sin systemd, sin red, sin loginctl. `calls` recoge los `systemctl`."""
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / "data" / "gateway.db").write_text("db")
    (home / "config.env").write_text("ADMIN_TOKEN=x\n")
    monkeypatch.setenv("JOTA_GATEWAY_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(cfg, "load", lambda: (SimpleNamespace(
        DATABASE_URL=f"sqlite:///{home / 'data' / 'gateway.db'}"), [], {}))
    monkeypatch.setattr(service, "require_systemd", lambda: None)
    monkeypatch.setattr(service, "port_free", lambda: True)
    monkeypatch.setattr(service, "linger_enabled", lambda user: True)
    monkeypatch.setattr(service, "_probe", lambda url: True)
    monkeypatch.setattr(service, "DOCKERENV", tmp_path / "no-dockerenv")
    monkeypatch.setattr(output, "_stdin_isatty", lambda: False)
    calls: list[tuple[str, ...]] = []

    state = SimpleNamespace(home=home, calls=calls, unit=service.unit_path(), tmp=tmp_path,
                            active=False)

    def fake(*args, check=True):
        if args[0] == "is-active":  # no cuenta como acción: solo sondea
            return subprocess.CompletedProcess(args, 0 if state.active else 3, "", "")
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(service, "systemctl", fake)
    return state


def run(*args, **kw):
    return CliRunner().invoke(cli, list(args), **kw)


def _unit_text(svc):
    return service.render_unit(Path(EXE), svc.home)


def test_install_refuses_without_db(svc):
    (svc.home / "data" / "gateway.db").unlink()
    r = run("install", "--exe", EXE)
    assert r.exit_code == 1
    assert "No hay BD en" in r.output and "jota-gw init" in r.output
    assert "migrate --from-docker" in r.output
    assert not svc.unit.exists() and svc.calls == []


def test_install_refuses_with_invalid_config(svc, monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: (None, ["PORT: int_parsing"], {}))
    r = run("install", "--exe", EXE)
    assert r.exit_code == 1
    assert "PORT: int_parsing" in r.output
    assert not svc.unit.exists() and svc.calls == []


def test_install_refuses_when_port_busy(monkeypatch):
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        monkeypatch.setattr(serve_mod, "PORT", srv.getsockname()[1])
        assert REAL_PORT_FREE() is False  # con SO_REUSEADDR sigue viendo un listener real
    assert REAL_PORT_FREE() is True  # libre al cerrar el que escucha


def test_install_stops_when_port_busy(svc, monkeypatch):
    monkeypatch.setattr(service, "port_free", lambda: False)
    r = run("install", "--exe", EXE)
    assert r.exit_code == 1
    assert "otro gateway" in r.output and "contenedor" in r.output
    assert not svc.unit.exists() and svc.calls == []


def test_install_active_service_same_unit_skips_port_check(svc, monkeypatch):
    svc.unit.parent.mkdir(parents=True)
    svc.unit.write_text(_unit_text(svc))
    svc.active = True
    monkeypatch.setattr(service, "port_free", lambda: False)  # el 8004 es nuestro
    before = svc.unit.stat().st_mtime_ns
    r = run("--json", "install", "--exe", EXE)
    assert r.exit_code == 0, r.output
    assert svc.unit.stat().st_mtime_ns == before
    assert svc.calls == [("enable", "--now", "jota-gateway.service")]


def test_install_active_service_new_unit_restarts(svc, monkeypatch):
    svc.unit.parent.mkdir(parents=True)
    svc.unit.write_text("viejo")
    svc.active = True
    monkeypatch.setattr(service, "port_free", lambda: False)
    r = run("--yes", "install", "--exe", EXE)
    assert r.exit_code == 0, r.output
    assert svc.unit.read_text() == _unit_text(svc)
    assert svc.calls == [("daemon-reload",), ("enable", "--now", "jota-gateway.service"),
                         ("restart", "jota-gateway.service")]


def test_install_inactive_existing_unit_and_port_busy_refuses(svc, monkeypatch):
    svc.unit.parent.mkdir(parents=True)
    svc.unit.write_text(_unit_text(svc))
    before = svc.unit.read_bytes()
    svc.active = False
    monkeypatch.setattr(service, "port_free", lambda: False)
    r = run("--yes", "install", "--exe", EXE)
    assert r.exit_code == 1 and "ocupado" in r.output
    assert svc.unit.read_bytes() == before and svc.calls == []


def test_install_without_unit_and_port_busy_still_refuses(svc, monkeypatch):
    svc.active = True  # da igual: sin unidad no se sondea
    monkeypatch.setattr(service, "port_free", lambda: False)
    assert run("install", "--exe", EXE).exit_code == 1
    assert svc.calls == []


def test_install_writes_unit_and_enables(svc):
    r = run("--json", "install", "--exe", EXE)
    assert r.exit_code == 0, r.output
    assert svc.unit.read_text() == _unit_text(svc)
    assert svc.calls == [("daemon-reload",), ("enable", "--now", "jota-gateway.service")]
    assert json.loads(r.stdout) == {"unit": str(svc.unit), "exe": EXE, "home": str(svc.home),
                                    "state": "instalado"}
    assert not list(svc.unit.parent.glob("*.tmp*"))  # escritura atómica sin restos


def test_install_same_unit_is_noop(svc):
    svc.unit.parent.mkdir(parents=True)
    svc.unit.write_text(_unit_text(svc))
    before = svc.unit.stat().st_mtime_ns
    r = run("--json", "install", "--exe", EXE)
    assert r.exit_code == 0, r.output
    assert svc.unit.stat().st_mtime_ns == before
    assert svc.calls == [("enable", "--now", "jota-gateway.service")]
    assert json.loads(r.stdout)["state"] == "sin cambios"


def test_install_overwrite_needs_confirm(svc):
    svc.unit.parent.mkdir(parents=True)
    svc.unit.write_text("viejo")
    r = run("install", "--exe", EXE)
    assert r.exit_code == 2 and "--yes" in r.output
    assert svc.unit.read_text() == "viejo" and svc.calls == []
    r = run("--yes", "install", "--exe", EXE)
    assert r.exit_code == 0, r.output
    assert svc.unit.read_text() == _unit_text(svc)


def test_install_warns_without_linger(svc, monkeypatch):
    monkeypatch.setenv("USER", "sito")
    monkeypatch.setattr(service, "linger_enabled", lambda user: False)
    r = run("install", "--exe", EXE)
    assert r.exit_code == 0, r.output
    assert "loginctl enable-linger sito" in r.output


def test_install_fails_with_hint_when_not_healthy(svc, monkeypatch):
    monkeypatch.setattr(service, "_probe", lambda url: False)
    monkeypatch.setattr(service, "HEALTH_TIMEOUT_S", 0.05)
    monkeypatch.setattr(service.time, "sleep", lambda s: None)
    r = run("install", "--exe", EXE)
    assert r.exit_code == 1 and "jota-gw logs -n 50" in r.output


def test_wait_healthy_retries_until_ok(svc, monkeypatch):
    answers = iter([False, False, True])
    monkeypatch.setattr(service, "_probe", lambda url: next(answers))
    monkeypatch.setattr(service.time, "sleep", lambda s: None)
    service.wait_healthy("http://127.0.0.1:8004")


def test_uninstall_keeps_home(svc):
    svc.unit.parent.mkdir(parents=True)
    svc.unit.write_text(_unit_text(svc))
    r = run("--yes", "--json", "uninstall")
    assert r.exit_code == 0, r.output
    assert not svc.unit.exists()
    assert svc.calls == [("disable", "--now", "jota-gateway.service"), ("daemon-reload",)]
    assert (svc.home / "data" / "gateway.db").read_text() == "db"
    assert (svc.home / "config.env").read_text() == "ADMIN_TOKEN=x\n"
    assert json.loads(r.stdout) == {"unit": str(svc.unit), "exe": None, "home": str(svc.home),
                                    "state": "desinstalado"}
    r = run("--yes", "uninstall")  # sin unidad: 0 y lo dice
    assert r.exit_code == 0 and "no está instalado" in r.output


def test_uninstall_says_where_data_stays(svc):
    svc.unit.parent.mkdir(parents=True)
    svc.unit.write_text(_unit_text(svc))
    r = run("--yes", "uninstall")
    assert str(svc.home) in r.output


def test_uninstall_needs_confirm_without_tty(svc):
    svc.unit.parent.mkdir(parents=True)
    svc.unit.write_text(_unit_text(svc))
    r = run("uninstall")
    assert r.exit_code == 2 and "--yes" in r.output
    assert svc.unit.exists() and svc.calls == []


@pytest.fixture()
def installed(svc):
    svc.unit.parent.mkdir(parents=True)
    svc.unit.write_text(_unit_text(svc))
    return svc


def test_start_stop_restart_call_systemctl(installed, monkeypatch):
    waits = []
    monkeypatch.setattr(service, "_probe", lambda url: waits.append(url) or True)
    assert run("start").exit_code == 0
    assert run("stop").exit_code == 0
    assert run("--yes", "--json", "restart").exit_code == 0
    assert installed.calls == [("start", "jota-gateway.service"), ("stop", "jota-gateway.service"),
                               ("restart", "jota-gateway.service")]
    assert waits == ["http://127.0.0.1:8004", "http://127.0.0.1:8004"]  # start y restart, no stop


def test_restart_needs_confirm(installed):
    r = run("restart")
    assert r.exit_code == 2 and "--yes" in r.output
    assert installed.calls == []


@pytest.mark.parametrize("verb", ["start", "stop", "restart"])
def test_without_unit_explains_docker(svc, verb):
    r = run("--yes", verb)
    assert r.exit_code == 1
    assert service.NO_SERVICE_MSG in r.output and "docker compose restart" in r.output
    assert svc.calls == []


def test_inside_container_explains_docker(installed, monkeypatch):
    dockerenv = installed.tmp / ".dockerenv"
    dockerenv.write_text("")
    monkeypatch.setattr(service, "DOCKERENV", dockerenv)
    r = run("--yes", "restart")
    assert r.exit_code == 1 and service.NO_SERVICE_MSG in r.output
    assert installed.calls == []
