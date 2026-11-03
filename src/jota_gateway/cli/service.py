"""
service.py
~~~~~~~~~~
Servicio de usuario de systemd de `jota-gw`: la unidad, la elección del ejecutable y los
comandos `install`, `uninstall`, `start`, `stop` y `restart`. `subprocess` se importa dentro
de las funciones que lo usan (`--help` < 150 ms).
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import click

from jota_gateway import paths
from jota_gateway.cli import api
from jota_gateway.cli.api import ApiError
from jota_gateway.cli.output import Out, confirm

if TYPE_CHECKING:
    from subprocess import CompletedProcess

UNIT_NAME = "jota-gateway.service"
HEALTH_TIMEOUT_S = 20.0
DOCKERENV = Path("/.dockerenv")
NO_SERVICE_MSG = ("No hay servicio instalado. En Docker: `docker compose restart` / "
                  "`docker compose logs -f`. Para instalarlo: `jota-gw install`.")

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
    # Solo <env> y <clon> (<clon>/.venv/bin/jota-gw): un pyproject.toml suelto en ~ o / no cuenta.
    if any((p / "pyproject.toml").is_file() for p in real.parents[1:3]):
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


def systemctl_or_fail(*args: str) -> None:
    import subprocess

    try:
        systemctl(*args)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip()
        raise ApiError(f"`systemctl --user {' '.join(args)}` ha fallado"
                       f"{f': {detail}' if detail else ''}.", 1) from None


def port_free() -> bool:
    """El puerto del gateway (127.0.0.1) no lo usa nadie: ni bind ni conexión."""
    import socket

    from jota_gateway.cli import serve

    with socket.socket() as s:
        # Sin esto, conexiones en TIME_WAIT tras un `docker compose stop` darían un falso «ocupado».
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", serve.PORT))
        except OSError:
            return False
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", serve.PORT)) != 0


def linger_enabled(user: str) -> bool:
    import subprocess

    try:
        r = subprocess.run(["loginctl", "show-user", user, "-p", "Linger"], text=True,
                           capture_output=True, check=False)
    except OSError:
        return False
    return r.stdout.strip() == "Linger=yes"


def _probe(url: str) -> bool:
    try:
        api.Api(url, "", timeout=2).request("GET", "/healthz")
    except ApiError:
        return False
    return True


def wait_healthy(url: str) -> None:
    """Espera hasta HEALTH_TIMEOUT_S a que `/healthz` responda; la usan install, start y restart."""
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while not _probe(url):
        if time.monotonic() >= deadline:
            raise ApiError(f"El servicio no responde en {HEALTH_TIMEOUT_S:.0f} s. "
                           "Mira los registros con `jota-gw logs -n 50`.", 1)
        time.sleep(0.5)


def _out(ctx: click.Context) -> Out:
    return Out(ctx.find_root().obj["json"])


@click.command()
@click.option("--exe", type=click.Path(path_type=Path), help="Ejecutable `jota-gw` de la unidad.")
@click.pass_context
def install(ctx: click.Context, exe: Path | None) -> None:
    """Instala y arranca el servicio."""
    from jota_gateway.cli import config as cfg

    out = _out(ctx)
    require_systemd()
    settings, _ = cfg._load_or_fail(out)
    db = paths.sqlite_file(settings.DATABASE_URL)
    if db is not None and not db.exists():
        raise ApiError(f"No hay BD en {db}. Ejecuta `jota-gw init` (instalación nueva) o "
                       "`jota-gw migrate --from-docker <carpeta>`.", 1)
    unit = unit_path()
    # Si nuestro servicio ya corre, el 8004 es suyo: se puede reinstalar o actualizar la unidad.
    active = unit.exists() and systemctl("is-active", "--quiet", UNIT_NAME,
                                         check=False).returncode == 0
    if not active and not port_free():
        raise ApiError(f"El puerto {_port()} está ocupado: ¿hay otro gateway (el contenedor)? "
                       "Páralo antes de instalar el servicio.", 1)
    exe = pick_exe(exe)
    home = paths.home()
    text = render_unit(exe, home)
    if unit.exists() and unit.read_text() == text:
        state = "sin cambios"
    else:
        if unit.exists():
            confirm(ctx, f"{unit} ya existe y es distinta. ¿Sobrescribirla?")
        unit.parent.mkdir(parents=True, exist_ok=True)
        tmp = unit.with_name(f".{UNIT_NAME}.tmp")
        tmp.write_text(text)
        os.replace(tmp, unit)  # atómico: systemd nunca ve una unidad a medias
        systemctl_or_fail("daemon-reload")
        state = "instalado"
    systemctl_or_fail("enable", "--now", UNIT_NAME)
    if active and state == "instalado":
        systemctl_or_fail("restart", UNIT_NAME)  # si no, seguiría corriendo el ejecutable viejo
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if not linger_enabled(user):
        out.error(f"Aviso: sin linger el servicio se para al cerrar tu sesión. "
                  f"Ejecuta `loginctl enable-linger {user}`.")
    wait_healthy(ctx.find_root().obj["url"])
    if out.as_json:
        out.json({"unit": str(unit), "exe": str(exe), "home": str(home), "state": state})
        return
    out.pairs([("unidad", unit), ("ejecutable", exe), ("base", home), ("estado", state)])
    out.message("Servicio en marcha.")


def _port() -> int:
    from jota_gateway.cli import serve

    return serve.PORT


@click.command()
@click.pass_context
def uninstall(ctx: click.Context) -> None:
    """Quita el servicio; no toca los datos."""
    out = _out(ctx)
    unit = unit_path()
    home = paths.home()
    if not unit.exists():
        if out.as_json:
            out.json({"unit": str(unit), "exe": None, "home": str(home), "state": "no instalado"})
        else:
            out.message(f"El servicio no está instalado ({unit}).")
        return
    require_systemd()
    confirm(ctx, f"¿Quitar el servicio {UNIT_NAME}?")
    systemctl_or_fail("disable", "--now", UNIT_NAME)
    unit.unlink()
    systemctl_or_fail("daemon-reload")
    if out.as_json:
        out.json({"unit": str(unit), "exe": None, "home": str(home), "state": "desinstalado"})
        return
    out.message(f"Servicio quitado. No se toca el directorio base ({home}): tus datos siguen ahí.")


def _require_unit() -> None:
    if DOCKERENV.exists() or not unit_path().exists():
        raise ApiError(NO_SERVICE_MSG, 1)
    require_systemd()


def _verb(ctx: click.Context, verb: str, state: str, wait: bool) -> None:
    _require_unit()
    systemctl_or_fail(verb, UNIT_NAME)
    if wait:
        wait_healthy(ctx.find_root().obj["url"])
    out = _out(ctx)
    if out.as_json:
        out.json({"unit": str(unit_path()), "state": state})
    else:
        out.message(f"Servicio {state}.")


@click.command()
@click.pass_context
def start(ctx: click.Context) -> None:
    """Arranca el servicio."""
    _verb(ctx, "start", "arrancado", wait=True)


@click.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
    """Para el servicio."""
    _verb(ctx, "stop", "parado", wait=False)


@click.command()
@click.pass_context
def restart(ctx: click.Context) -> None:
    """Reinicia el servicio."""
    _require_unit()  # antes de preguntar: sin servicio no hay nada que reiniciar
    confirm(ctx, f"¿Reiniciar {UNIT_NAME}? Se cortarán las sesiones abiertas.")
    _verb(ctx, "restart", "reiniciado", wait=True)
