"""
migrate.py
~~~~~~~~~~
`fogar-gw migrate --from-docker DIR`: copia una instalación Docker (`DIR/.env` y
`DIR/data/gateway.db`) a la nativa (`<base>/config.env` y `<base>/data/gateway.db`).

Es la única excepción, junto a `init` y `serve --create-db`, a «ninguna orden crea la BD»: aquí
la BD nace como copia (API de backup de sqlite3), sin migraciones. El origen solo se abre en
solo lectura y nunca se escribe. Si algo falla no queda nada a medias.
"""

import fcntl
import os
import re
from io import StringIO
from pathlib import Path
from typing import Any

import click

from fogar_gateway import paths
from fogar_gateway.cli import api, doctor
from fogar_gateway.cli import config as cfg
from fogar_gateway.cli.output import Out

# Solo los usa compose, o los fija la base nativa.
DROP = ("DATABASE_URL", "GATEWAY_UID", "GATEWAY_GID")
DOCKER_BASE = "/app"  # FOGAR_GATEWAY_HOME de la imagen
ADD = ("LOG_FORMAT", "json")  # la imagen lo fijaba con ENV: los logs siguen igual

_DROP_RE = re.compile(rf"\s*(?:export\s+)?({'|'.join(DROP)})\s*=", re.IGNORECASE)
_ADD_RE = re.compile(rf"\s*(?:export\s+)?{ADD[0]}\s*=", re.IGNORECASE)


def _ro(path: Path) -> Any:
    import sqlite3

    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _source_revision(db: Path) -> str | None:
    import sqlite3

    try:
        con = _ro(db)
        try:
            rows = con.execute("SELECT version_num FROM alembic_version").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        raise api.ApiError(f"{db} no es una BD del gateway o está dañada ({type(e).__name__}).") from None
    if len(rows) != 1:
        raise api.ApiError(f"{db}: alembic_version debe tener exactamente una fila y tiene {len(rows)}.")
    return str(rows[0][0])


def _check_database_url(text: str) -> None:
    """Un DATABASE_URL del .env solo vale si apunta al fichero que se copia (base /app)."""
    from dotenv import dotenv_values

    url = dotenv_values(stream=StringIO(text)).get("DATABASE_URL")
    if not url:
        return
    saved = os.environ.get("FOGAR_GATEWAY_HOME")
    os.environ["FOGAR_GATEWAY_HOME"] = DOCKER_BASE
    try:
        target = paths.sqlite_file(url)
    finally:
        if saved is None:
            del os.environ["FOGAR_GATEWAY_HOME"]
        else:
            os.environ["FOGAR_GATEWAY_HOME"] = saved
    if target != Path(DOCKER_BASE) / "data" / "gateway.db":
        raise api.ApiError(
            f"El DATABASE_URL del .env no apunta a {DOCKER_BASE}/data/gateway.db sino a "
            f"{target or 'una BD que no es un fichero SQLite'}: migrate solo copia data/gateway.db. "
            "Corrige el .env o copia esa BD a mano.")


def _copy_db(src: Path, tmp: Path, dest: Path) -> int:
    import sqlite3

    source = _ro(src)
    target = sqlite3.connect(tmp)
    try:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise api.ApiError("La copia de la BD no pasa integrity_check.")
        wanted = source.execute("SELECT id FROM clients ORDER BY id").fetchall()
        if target.execute("SELECT id FROM clients ORDER BY id").fetchall() != wanted:
            raise api.ApiError("La copia de la BD no tiene los mismos clientes que el origen.")
    finally:
        target.close()
        source.close()
    os.replace(tmp, dest)
    return len(wanted)


def _convert_env(text: str) -> tuple[str, list[str], list[str]]:
    """Quita DROP y añade LOG_FORMAT si falta; el resto de líneas, tal cual."""
    kept: list[str] = []
    dropped: list[str] = []
    for line in text.splitlines(keepends=True):
        m = _DROP_RE.match(line)
        if m:
            dropped.append(m.group(1))
        else:
            kept.append(line)
    added: list[str] = []
    if not any(_ADD_RE.match(line) for line in kept):
        if kept and not kept[-1].endswith("\n"):
            kept[-1] += "\n"
        kept.append(f"{ADD[0]}={ADD[1]}\n")
        added.append(f"{ADD[0]}={ADD[1]}")
    return "".join(kept), dropped, added


def _config_errors(path: Path) -> list[str]:
    from dotenv import dotenv_values

    # Sin esas variables en el entorno (como `config set`): el servicio no hereda esta shell,
    # así que una variable de ella no debe tapar un error del fichero migrado.
    names = {k.upper() for k in dotenv_values(path)}
    saved = {n: os.environ.pop(n) for n in list(os.environ) if n.upper() in names}
    try:
        return cfg.load(path)[1]
    finally:
        os.environ.update(saved)


def _read_env(path: Path) -> str:
    try:
        return path.read_text()
    except OSError as e:
        raise api.ApiError(f"No se puede leer {path} ({type(e).__name__}).") from None
    except ValueError:  # UnicodeDecodeError
        raise api.ApiError(f"{path} no está en UTF-8: conviértelo (p. ej. con `iconv -f latin1 -t utf-8`) y repite.") from None


def _lock_source(lock: Path) -> int | None:
    """Candado compartido sobre el gateway.lock de Docker: si falla, el gateway sigue en marcha."""
    if not lock.exists():
        return None
    try:
        fd = os.open(lock, os.O_RDONLY)
    except OSError as e:
        raise api.ApiError(f"No se puede leer {lock} ({type(e).__name__}): comprueba su dueño y sus "
                           "permisos, o para el contenedor (`docker compose stop`) y repite.") from None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise api.ApiError("El gateway de Docker sigue en marcha: para el contenedor "
                           "(`docker compose stop`) antes de migrar.") from None
    return fd


def _copy_private(src: Path, tmp: Path, dst: Path) -> None:
    """Copia `src` (abierto solo para leer) a `dst` vía un temporal 0600 con fsync y rename: en
    destino nunca hay un fichero a medias ni legible por otros."""
    data = src.read_bytes()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dst)


@click.command()
@click.option("--from-docker", "source", required=True, metavar="DIR",
              type=click.Path(path_type=Path), help="Directorio de la instalación Docker.")
@click.pass_context
def migrate(ctx: click.Context, source: Path) -> None:
    """Copia una instalación Docker a la nativa."""
    import sqlite3

    out = Out(ctx.find_root().obj["json"])
    source = source.expanduser().absolute()
    env_src, db_src = source / ".env", source / "data" / "gateway.db"
    for f in (env_src, db_src):
        if not f.is_file():
            raise api.ApiError(f"No existe {f}: ¿es el directorio de una instalación Docker?")
    base = paths.home()
    config, data = paths.config_file(), base / "data"
    db = data / "gateway.db"
    # La identidad ante OpenClaw (M19), si la instalación de Docker tiene una: con ella el
    # gateway nativo sigue emparejado. Nunca se pisa una que ya exista aquí.
    device_src, device = source / "data" / paths.OPENCLAW_DEVICE_FILENAME, paths.openclaw_device_file()
    has_device = device_src.is_file()
    leftovers = [f for f in (config, db, *(db.with_name(db.name + x) for x in ("-journal", "-wal", "-shm")),
                             *((device,) if has_device else ()))
                 if f.exists()]
    if leftovers:
        raise api.ApiError(f"Ya hay una instalación en {base} ({leftovers[0].name}); no se sobrescribe.")
    lock_fd = _lock_source(source / "data" / paths.LOCK_FILENAME)
    old_umask = os.umask(0o077)  # 700 y 600 desde el primer momento
    made = [d for d in (base, data) if not d.exists()]
    tmp_db, tmp_cfg = db.with_name(db.name + ".tmp"), config.with_name(config.name + ".tmp")
    tmp_device = device.with_name(device.name + ".tmp")
    device_copied = False
    done = False
    try:
        text = _read_env(env_src)
        _check_database_url(text)
        revision = _source_revision(db_src)
        _, head = doctor.revisions()
        if head is None:
            raise api.ApiError("No se encuentran las migraciones del paquete (o hay varias cabezas): "
                               "no se puede comprobar la versión de la BD.")
        if revision != head:
            raise api.ApiError(
                f"La BD de Docker está en la migración {revision} y este fogar-gw espera {head}. "
                "Despliega la misma versión en Docker o instala la misma versión nativa.")
        data.mkdir(parents=True, exist_ok=True)
        clients = _copy_db(db_src, tmp_db, db)
        new, dropped, added = _convert_env(text)
        tmp_cfg.write_text(new)
        os.replace(tmp_cfg, config)
        errors = _config_errors(config)
        if errors:
            raise api.ApiError("La configuración migrada no es válida: " + "; ".join(errors)
                               + ". No se ha creado nada.")
        if has_device:
            _copy_private(device_src, tmp_device, device)
            device_copied = True
        done = True
    except (sqlite3.Error, OSError) as e:
        raise api.ApiError(f"Ha fallado la migración ({type(e).__name__}). No se ha creado nada.") from None
    finally:
        os.umask(old_umask)
        if lock_fd is not None:
            os.close(lock_fd)
        if not done:
            for f in (tmp_db, tmp_cfg, tmp_device, db, config):
                f.unlink(missing_ok=True)
            if device_copied:
                device.unlink(missing_ok=True)
            for d in reversed(made):
                try:
                    d.rmdir()
                except OSError:  # no vacía: no es nuestra
                    pass

    if out.as_json:
        out.json({"from": str(source), "config": {"path": str(config), "dropped": dropped, "added": added},
                  "db": {"path": str(db), "revision": revision, "clients": clients},
                  "openclaw_device": str(device) if device_copied else None})
        return
    out.pairs([("origen", source), ("configuración", config), ("quitado", ", ".join(dropped) or "nada"),
               ("añadido", ", ".join(added) or "nada"), ("BD", f"{db} · migración {revision} · {clients} clientes"),
               *([("identidad OpenClaw", device)] if device_copied else [])])
    out.message("Siguiente paso: fogar-gw install")
