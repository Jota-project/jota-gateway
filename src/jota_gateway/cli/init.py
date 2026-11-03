"""
init.py
~~~~~~~
`jota-gw init`: prepara una instalación nueva (decisión 5 de M14c: el gateway no crea nunca
una BD por su cuenta). Crea el directorio base (700), `config.env` (600) con un
`ADMIN_TOKEN` aleatorio y la BD migrada. No toca nada que ya exista.
"""

import os
import secrets

import click

from jota_gateway import paths
from jota_gateway.cli import config as cfg
from jota_gateway.cli.output import Out


@click.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Prepara una instalación nueva; no toca lo que ya exista."""
    out = Out(ctx.find_root().obj["json"])
    os.umask(0o077)  # 700 y 600 desde el primer momento
    paths.home().mkdir(parents=True, exist_ok=True)
    config = paths.config_file()
    try:
        with open(config, "x") as f:
            f.write(f"ADMIN_TOKEN={secrets.token_urlsafe(32)}\n")
        config_state = "creada, con un ADMIN_TOKEN aleatorio"
    except FileExistsError:
        config_state = "ya existía, no se toca"

    settings, errors, _ = cfg.load()
    if errors:
        out.error(f"La configuración no es válida: {'; '.join(errors)}. "
                  "Corrígela y vuelve a ejecutar `jota-gw init`.")
        raise click.exceptions.Exit(1)
    db = paths.sqlite_file(settings.DATABASE_URL)
    if db is None:
        db_state = "DATABASE_URL no es un fichero SQLite: no se crea nada"
    elif db.exists():
        db_state = "ya existía, no se toca (se migra sola al arrancar)"
    else:
        from jota_gateway.db import database

        database.CREATE_DB = True
        try:
            database.run_migrations()
        finally:
            database.dispose_engine()
        db_state = "creada y migrada"

    if out.as_json:
        out.json({"base": str(paths.home()), "config": {"path": str(config), "state": config_state},
                  "db": {"path": str(db) if db else None, "state": db_state}})
        return
    out.pairs([("base", paths.home()), ("configuración", f"{config}: {config_state}"),
               ("BD", f"{db}: {db_state}" if db else db_state)])
    out.message("Siguiente paso: completa config.env (OPENCLAW_TOKEN…) y arranca con `jota-gw serve`.")
