"""
logs.py
~~~~~~~
`jota-gw logs`: lee el journal del servicio de usuario. journald da la misma prioridad a todo
lo que sale por stdout, así que el nivel y la sesión se filtran aquí, en los dos formatos de
`core/logging.py` (JSON y texto). `subprocess` se importa dentro de la función (`--help` < 150 ms).
"""

import json
import os
import shutil

import click

from jota_gateway.cli import service
from jota_gateway.cli.api import ApiError

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _level_of(line: str) -> str | None:
    """Nivel de la línea (JSON `level` o 3.er campo de `ts | logger | LEVEL | msg`), si lo hay."""
    if line.startswith("{"):
        try:
            data = json.loads(line)
        except ValueError:
            return None
        return str(data["level"]) if isinstance(data, dict) and "level" in data else None
    parts = line.split(" | ", 3)
    return parts[2] if len(parts) == 4 else None


def matches(line: str, min_level: str | None, session: str | None) -> bool:
    """Si la línea pasa los filtros. Lo que no se entiende pasa sin filtrar."""
    if min_level is not None:
        level = _level_of(line)
        if level in LEVELS and LEVELS.index(level) < LEVELS.index(min_level.upper()):
            return False
    if session is not None:
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                return True
            if isinstance(data, dict):
                return session in (data.get("session_id"), data.get("client_id"))
            return True
        return session in line
    return True


@click.command()
@click.option("--follow", "-f", is_flag=True, help="Sigue los registros nuevos.")
@click.option("--lines", "-n", type=click.IntRange(min=0), default=100, show_default=True,
              help="Líneas del final a mostrar.")
@click.option("--level", type=click.Choice(LEVELS, case_sensitive=False),
              help="Nivel mínimo.")
@click.option("--session", metavar="ID", help="Solo esta sesión o cliente.")
def logs(follow: bool, lines: int, level: str | None, session: str | None) -> None:
    """Registros del servicio."""
    service.require_unit()
    if shutil.which("journalctl") is None:
        raise ApiError(service.NO_SERVICE_MSG, 1)
    cmd = ["journalctl", "--user", "-u", service.UNIT_NAME, "-o", "cat", "-n", str(lines)]
    if follow:
        cmd.append("-f")
    if level is None and session is None:
        os.execvp("journalctl", cmd)
        return
    import subprocess

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if matches(line.rstrip("\n"), level, session):
                click.echo(line, nl=False)
    except KeyboardInterrupt:
        pass  # Ctrl+C es la forma normal de dejar `-f`: sin traceback, salida 0
    finally:
        proc.terminate()
        proc.wait()
