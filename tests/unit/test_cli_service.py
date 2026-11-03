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
