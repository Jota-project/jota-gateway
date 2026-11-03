"""Directorio base de la instalación (decisión «Rutas de configuración y datos», regla 1).

`JOTA_GATEWAY_HOME` o `~/.jota-gateway`; nunca el directorio actual. `--home` llega en 14c.
"""

import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("JOTA_GATEWAY_HOME") or Path.home() / ".jota-gateway").expanduser()


def config_file() -> Path:
    return home() / "config.env"
