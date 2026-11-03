"""
service.py
~~~~~~~~~~
Servicio de usuario de systemd de `jota-gw`: la unidad y la elección del ejecutable. Funciones
puras (los comandos `install`, `start`… las usan); `subprocess` se importa dentro de `systemctl`.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from subprocess import CompletedProcess

from jota_gateway.cli.api import ApiError

UNIT_NAME = "jota-gateway.service"

# TimeoutStopSec = stop_grace_period de docker-compose.yml; mayor que serve.GRACEFUL_SHUTDOWN_S.
# Sin --create-db ni LOG_FORMAT: manda la configuración. Exit 3 = fallo de arranque que
# reintentar no arregla (RestartPreventExitStatus).
_UNIT = """[Unit]
Description=Jota gateway
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=exec
ExecStart={exe} serve
Environment=JOTA_GATEWAY_HOME={home}
Restart=on-failure
RestartSec=5
RestartPreventExitStatus=3
TimeoutStopSec=45
UMask=0077

[Install]
WantedBy=default.target
"""


def unit_path() -> Path:
    config = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config) / "systemd" / "user" / UNIT_NAME


def render_unit(exe: Path, home: Path) -> str:
    return _UNIT.format(exe=exe, home=home)


def _in_checkout(exe: Path) -> bool:
    real = exe.resolve()
    if any((p / "pyproject.toml").is_file() for p in real.parents):
        return True
    # shortcut: solo mira el site-packages junto al ejecutable (<env>/bin/jota-gw), el
    # layout de uv tool y de venv; otro layout no se detecta, hay que pasar --exe a mano.
    site = "lib*/python*/site-packages/jota[_-]gateway-*.dist-info/direct_url.json"
    return any(json.loads(f.read_text()).get("dir_info", {}).get("editable") is True
               for f in real.parent.parent.glob(site))


def pick_exe(explicit: Path | None) -> Path:
    """El `jota-gw` de uv tool. Sin resolver el symlink: así sobrevive a `uv tool upgrade`."""
    if explicit is not None:
        return explicit
    found = shutil.which("jota-gw")
    if found is None:
        raise ApiError("No encuentro `jota-gw` en el PATH; instálalo con `uv tool install` "
                       "o indica la ruta con --exe.", 1)
    exe = Path(found)
    if _in_checkout(exe):
        raise ApiError(f"{exe} está dentro de un clon o es una instalación editable: la unidad "
                       "se rompería al mover o borrar el clon. Instala con `uv tool install` "
                       "o indica otro ejecutable con --exe.", 1)
    return exe


def systemctl(*args: str, check: bool = True) -> "CompletedProcess[str]":
    import subprocess

    return subprocess.run(["systemctl", "--user", *args], check=check, text=True,
                          capture_output=True)


def require_systemd() -> None:
    if sys.platform != "linux" or shutil.which("systemctl") is None:
        raise ApiError("Este comando necesita Linux con systemd; en Docker usa "
                       "`docker compose up -d`, `docker compose logs`, etc.", 1)
