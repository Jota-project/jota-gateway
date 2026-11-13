"""
doctor.py
~~~~~~~~~
`fogar-gw doctor`: diagnóstico de la instalación, con el gateway en marcha o parado.

Cada comprobación sale como ok, warn, fail o info, con qué hacer. Nunca crea ni escribe la
base de datos (se abre en solo lectura) ni toma el candado (se lee en /proc/locks).
"""

import os
import re
import socket
import sqlite3
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import click

from fogar_gateway import paths
from fogar_gateway.cli import api
from fogar_gateway.cli import config as cfg
from fogar_gateway.cli.output import ACCENT, ERROR, MUTED, TEXT, Out

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
TCP_TIMEOUT_S = 2.0
VOICE_OFF = "voz desactivada: configúrala con `fogar-gw onboard`"
# El proceso sigue en `off` pero config.env ya tiene un proveedor: falta reiniciar, no el onboard.
VOICE_OFF_STALE = "voz desactivada en el gateway en marcha; config.env ya la tiene: `fogar-gw restart`"

Check = dict[str, str]


def _c(level: str, check: str, detail: str) -> Check:
    return {"level": level, "check": check, "detail": detail}


def revisions() -> tuple[set[str], str | None]:
    # shortcut: lee revision/down_revision de los ficheros; alembic.script cuesta ~360 ms.
    revs: set[str] = set()
    downs: set[str] = set()
    if not MIGRATIONS.is_dir():
        return revs, None
    for f in MIGRATIONS.glob("*.py"):
        text = f.read_text()
        rev = re.search(r'^revision[^=]*=\s*["\']([^"\']+)', text, re.M)
        down = re.search(r'^down_revision[^=]*=\s*["\']([^"\']+)', text, re.M)
        if rev:
            revs.add(rev.group(1))
        if down:
            downs.add(down.group(1))
    heads = revs - downs
    return revs, heads.pop() if len(heads) == 1 else None


def _check_db(path: Path | None, url: str) -> Check:
    if path is None:
        return _c("info", "base de datos", f"{url.split(':', 1)[0]}: no es un fichero SQLite.")
    if not path.is_file():
        return _c(
            "fail", "base de datos",
            f"No existe {path}: el gateway no arrancará. Si es una instalación nueva, créala "
            "con `fogar-gw init`; si ya tenías una, copia aquí su gateway.db.",
        )
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        finally:
            con.close()
    except sqlite3.Error as e:
        return _c("fail", "base de datos",
                  f"{path} no es una BD del gateway o está dañada ({type(e).__name__}).")
    current = row[0] if row else None
    known, head = revisions()
    if head is None:
        return _c("info", "base de datos", f"{path} · migración {current}; la última, no "
                  f"comprobado (no se encuentran las migraciones en {MIGRATIONS} o hay varias).")
    if current is None:
        return _c("fail", "base de datos", f"{path}: alembic_version está vacía.")
    if current == head:
        return _c("ok", "base de datos", f"{path} · migración {current} (la última)")
    if current in known:
        return _c("warn", "base de datos",
                  f"{path} · migración {current}, la última es {head}: "
                  "se aplicará sola al arrancar el gateway.")
    return _c("fail", "base de datos",
              f"{path} · migración {current} desconocida: la BD es de una versión más nueva "
              "del gateway. Actualiza el gateway antes de arrancarlo.")


def _check_permissions(items: list[tuple[Path, int]]) -> Check:
    fixes, checked = [], 0
    for path, wanted in items:
        # Solo lo que es del usuario: en Docker /app es de root y no se puede cambiar.
        if not path.exists() or path.stat().st_uid != os.getuid():
            continue
        checked += 1
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            fixes.append(f"chmod {wanted:o} {path}")
    if not checked:
        return _c("info", "permisos", "Nada que comprobar (no existe o no es de este usuario).")
    if not fixes:
        return _c("ok", "permisos", "Solo los puede leer su dueño (600/700).")
    return _c("warn", "permisos", "Los pueden leer otros usuarios. Arréglalo con: "
              + "; ".join(fixes))


def _lock_held(lock: Path) -> bool | None:
    """¿Lo tiene algún proceso? Leyendo /proc/locks, sin tomarlo; None fuera de Linux."""
    locks = Path("/proc/locks")
    if not locks.exists():
        return None
    if not lock.exists():
        return False
    # Solo el inodo («MAJ:MIN:INODO»): en overlayfs (Docker) el dispositivo de stat no
    # coincide con el de /proc/locks.
    inode = f":{lock.stat().st_ino}"
    return any(
        field.endswith(inode) for line in locks.read_text().splitlines() for field in line.split()
    )


def _tcp(address: str) -> bool:
    host, _, port = address.rpartition(":")
    try:
        with socket.create_connection((host or address, int(port)), timeout=TCP_TIMEOUT_S):
            return True
    except (OSError, ValueError):
        return False


def _voice_off(settings: Any) -> set[str]:
    """Servicios de voz en `off`: no se sondean y su línea es informativa."""
    providers = (("STT", settings.STT_PROVIDER), ("TTS", settings.TTS_PROVIDER)) if settings else ()
    return {name for name, provider in providers if provider == "off"}


def _live_off(state: Any, settings: Any) -> set[str]:
    """Con el gateway en marcha manda el proceso (/admin/state), no config.env: puede no haberse
    reiniciado tras cambiarla. Si no se puede leer, la config local."""
    try:
        services = state["services"]
        return {name for name, key in (("STT", "stt"), ("TTS", "tts"))
                if services[key]["provider"] == "off"}
    except (KeyError, TypeError):  # sin token válido (None) o una respuesta sin esos campos
        return _voice_off(settings)


def _services_offline(settings: Any) -> list[Check]:
    """Con el gateway parado: ¿aceptan conexiones las direcciones configuradas?"""
    eleven = f"{urlsplit(settings.ELEVENLABS_BASE_URL).hostname or 'api.elevenlabs.io'}:443"
    targets = [
        ("orquestador", f"{settings.OPENCLAW_HOST}:{settings.OPENCLAW_PORT}", "fail"),
        ("STT", eleven if settings.STT_PROVIDER == "elevenlabs" else settings.TRANSCRIBER_WS_URL,
         "warn"),
        ("TTS", eleven if settings.TTS_PROVIDER == "elevenlabs" else settings.TTS_WS_URL, "warn"),
    ]
    off = _voice_off(settings)
    return [
        _c("info", name, VOICE_OFF)
        if name in off
        else _c("ok", name, f"{address} acepta conexiones (no prueba el servicio).")
        if _tcp(address)
        else _c(level, name, f"Nadie escucha en {address}.")
        for name, address, level in targets
    ]


def _services_online(client: api.Api, off: set[str], configured_off: set[str]) -> list[Check]:
    ready = client.request("GET", "/ready", accept=(503,))
    services = ready.get("services", {})
    result = []
    for name, key, level in (("orquestador", "orchestrator", "fail"), ("STT", "transcriber", "warn"),
                             ("TTS", "tts", "warn")):
        if name in off:
            result.append(_c("info", name, VOICE_OFF if name in configured_off else VOICE_OFF_STALE))
            continue
        state = services.get(key, "?")
        ok = state == "ok"
        detail = "disponible" if ok else f"{state}: el gateway no lo alcanza"
        result.append(_c("ok" if ok else level, name, detail))
    return result


ACCESS = "acceso a OpenClaw"
_AUTH = {"shared_token": "token compartido", "device_token": "token de dispositivo"}


def _scopes(scopes: Any) -> str:
    return ", ".join(str(s) for s in scopes) if isinstance(scopes, list) and scopes else "ninguno"


def _access_from_state(orch: dict[str, Any]) -> Check:
    """Lo que dice el gateway en marcha (/admin/state, M19)."""
    if orch.get("pairing") == "pending":
        rid = orch.get("pairing_request_id") or "--latest"
        return _c("warn", ACCESS, "OpenClaw espera que apruebes este gateway como dispositivo. En "
                  f"la máquina de OpenClaw: `openclaw devices approve {rid}`; el gateway se "
                  "conecta solo al aprobarlo.")
    how = _AUTH.get(str(orch.get("auth_method")), "?")
    paired = " · emparejado como dispositivo" if orch.get("pairing") == "paired" else ""
    if orch.get("admin"):
        return _c("ok", ACCESS, f"Con permisos de administración ({how}{paired}).")
    return _c("info", ACCESS, f"Sin permisos de administración ({how}{paired}; permisos: "
              f"{_scopes(orch.get('scopes'))}). Hoy no hacen falta para hablar.")


def _access_from_file(settings: Any) -> Check:
    """Con el gateway parado (o sin ese dato): la identidad guardada, sin cargar `cryptography`
    ni enseñar la clave o el token."""
    from fogar_gateway.services.openclaw.device import DeviceFileError, DeviceStore

    store = DeviceStore(paths.openclaw_device_file())
    try:
        summary = store.summary()
    except DeviceFileError:
        return _c("warn", ACCESS, f"{store.path} no es una identidad válida: el gateway conecta sin "
                  "ella (solo vale con OpenClaw en esta máquina). Si no estaba emparejado, bórralo "
                  "y el gateway creará otra.")
    if not summary["exists"]:
        return _c("info", ACCESS, "Sin identidad de dispositivo todavía: el gateway la crea al "
                  "conectar con OpenClaw.")
    device = (summary["device_id"] or "?")[:12]
    gateway = f"{settings.OPENCLAW_HOST}:{settings.OPENCLAW_PORT}" if settings else None
    if summary["token_gateway"] and summary["token_gateway"] == gateway:
        return _c("info", ACCESS, f"Dispositivo {device}, emparejado con {gateway} (permisos: "
                  f"{_scopes(summary['token_scopes'])}).")
    return _c("info", ACCESS, f"Dispositivo {device}, sin emparejar con {gateway or 'OpenClaw'} (con "
              "OpenClaw en esta máquina no hace falta).")


def _check_openclaw_access(state: Any, settings: Any) -> Check:
    orch = state.get("services", {}).get("orchestrator") if isinstance(state, dict) else None
    if isinstance(orch, dict) and orch.get("pairing") not in (None, "unknown"):
        return _access_from_state(orch)
    return _access_from_file(settings)


def run_checks(url: str, token: str | None) -> list[Check]:
    out: list[Check] = []
    settings, errors, _ = cfg.load()
    if errors:
        out.append(_c("fail", "configuración", "No es válida: " + "; ".join(errors)))
    elif not paths.config_file().is_file():
        out.append(_c("warn", "configuración", f"No existe {paths.config_file()}: solo cuentan "
                      "las variables del proceso y los valores por defecto."))
        out.extend(cfg.validation_checks(settings))
    else:
        out.append(_c("ok", "configuración", f"Válida ({paths.config_file()})."))
        out.extend(cfg.validation_checks(settings))

    db = paths.sqlite_file(settings.DATABASE_URL) if settings else None
    lock = db.parent / paths.LOCK_FILENAME if db else None
    out.append(_c("info", "rutas", " · ".join(f"{k}: {v}" for k, v in (
        ("base", paths.home()), ("configuración", paths.config_file()), ("BD", db),
        ("candado", lock), ("migraciones", MIGRATIONS)) if v)))

    held = None
    if settings:
        out.append(_check_db(db, settings.DATABASE_URL))
        perms = [(paths.home(), 0o700), (paths.config_file(), 0o600),
                 (paths.openclaw_device_file(), 0o600)]
        if db:
            perms += [(db.parent, 0o700), (db, 0o600)]
        out.append(_check_permissions(perms))
        held = _lock_held(lock) if lock else None
        detail = {None: "no comprobado (solo en Linux)", True: "lo tiene un gateway en marcha",
                  False: "libre: no hay ningún gateway usando esta BD"}[held]
        out.append(_c("info", "candado", detail))

    client = api.Api(url, token or "")
    try:
        client.request("GET", "/healthz")
    except api.ApiError as e:
        hint = " Pero el candado está tomado: ¿es otro puerto? revisa --url." if held else ""
        why = f"No responde en {urlsplit(url).netloc}." if e.exit_code == 3 else e.message
        out.append(_c("warn", "gateway", f"{why}{hint}"))
        if settings:
            out.extend(_services_offline(settings))
            out.append(_check_openclaw_access(None, settings))
        return out
    out.append(_c("ok", "gateway", f"Responde en {urlsplit(url).netloc}."))
    state = None
    try:
        client.token = api.resolve_token(token)
        state = client.request("GET", "/admin/state")
        out.append(_c("ok", "token", "El gateway acepta el token de administración."))
    except api.ApiError as e:
        out.append(_c("fail", "token", e.message))
    configured_off = _voice_off(settings)
    out.extend(_services_online(client, _live_off(state, settings), configured_off))
    out.append(_check_openclaw_access(state, settings))
    return out


_MARKS = {"ok": ("✓", TEXT), "warn": ("!", ACCENT), "fail": ("✗", ERROR), "info": ("·", MUTED)}


@click.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Diagnostica la instalación; sale con 1 si algo falla."""
    obj = ctx.find_root().obj
    out = Out(obj["json"])
    results = run_checks(obj["url"], obj["token"])
    if out.as_json:
        out.json(results)
    else:
        for c in results:
            mark, color = _MARKS[c["level"]]
            out.message(f"{mark} {c['check']}: {c['detail']}", color)
    if any(c["level"] == "fail" for c in results):
        raise click.exceptions.Exit(1)
