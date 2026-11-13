"""Dónde está cada cosa (decisión «Rutas de configuración y datos»).

Lo comparten el gateway y la CLI. Solo biblioteca estándar: `fogar-gw --help` lo importa y
debe seguir por debajo de 150 ms. Nada se resuelve contra el directorio actual.
"""

import os
import re
from pathlib import Path
from urllib.parse import quote, unquote

LOCK_FILENAME = "gateway.lock"
OPENCLAW_DEVICE_FILENAME = "openclaw-device.json"

# sqlite:///<ruta>[?query]. `sqlite://` y `:memory:` son bases en memoria.
_SQLITE_URL = re.compile(r"^(sqlite(?:\+\w+)?):///([^?]*)(\?.*)?$")


def home() -> Path:
    """`FOGAR_GATEWAY_HOME` (o `--home`) o `~/.fogar-gateway`, siempre absoluta."""
    return Path(os.environ.get("FOGAR_GATEWAY_HOME") or Path.home() / ".fogar-gateway").expanduser().absolute()


def config_file() -> Path:
    return home() / "config.env"


def openclaw_device_file() -> Path:
    """Identidad de dispositivo del gateway ante OpenClaw (clave Ed25519 y, si lo emparejan,
    su token de dispositivo): una credencial, 0600, fuera de config.env. En `<base>/data`, junto
    a la BD por defecto: en Docker es el volumen (`/app` no es escribible ni persiste)."""
    return home() / "data" / OPENCLAW_DEVICE_FILENAME


def absolute_database_url(url: str) -> str:
    """Una ruta SQLite relativa pasa a colgar del directorio base; el resto, igual.

    La ruta va codificada (`%23`, `%3F`, `%25`…): SQLAlchemy la decodifica, así una base con
    `#`, `?` o `%` no se confunde con la query.
    """
    m = _SQLITE_URL.match(url)
    if not m or not m.group(2) or m.group(2) == ":memory:" or m.group(2).startswith("/"):
        return url
    path = home() / unquote(m.group(2))
    return f"{m.group(1)}:///{quote(str(path), safe='/')}{m.group(3) or ''}"


def sqlite_file(url: str) -> Path | None:
    """Fichero de la BD, tal como lo abrirá SQLAlchemy; None si no es un fichero SQLite."""
    m = _SQLITE_URL.match(absolute_database_url(url))
    if not m or not m.group(2) or m.group(2) == ":memory:":
        return None
    return Path(unquote(m.group(2)))
