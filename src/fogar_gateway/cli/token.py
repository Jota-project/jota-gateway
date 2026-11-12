"""`fogar-gw token`: el token de administración, el que se pega en el dashboard.

Solo lo escribe en una terminal: en una tubería, un fichero o con --json acabaría en un log o en
un fichero por descuido. No lleva el secreto como argumento, así que tampoco queda en el
historial de la shell.
"""

import click

from fogar_gateway import paths
from fogar_gateway.cli import api
from fogar_gateway.cli.output import MUTED, Out

WARNING = ("Es la llave de administración de tu gateway: quien lo tenga lo controla. "
           "No lo compartas ni lo pegues en chats o capturas.")


@click.command()
@click.pass_context
def token(ctx: click.Context) -> None:
    """El token del dashboard (ADMIN_TOKEN), solo en una terminal."""
    from fogar_gateway.cli import config as cfg
    from fogar_gateway.cli import output

    out = Out(ctx.find_root().obj["json"])
    where = f"{paths.config_file()} (ADMIN_TOKEN)"
    if out.as_json or not output._isatty():
        raise api.ApiError("El token solo se escribe en una terminal, para que no acabe en un "
                           f"fichero o en un log. Está en {where}.", 1)
    settings, _ = cfg._load_or_fail(out)
    if not settings.ADMIN_TOKEN:
        raise api.ApiError(f"ADMIN_TOKEN está vacío: /admin y el dashboard no funcionan. "
                           f"Defínelo en {paths.config_file()} o ejecuta `fogar-gw init`.", 1)
    click.echo(settings.ADMIN_TOKEN)  # tal cual, sin estilo: se copia entero
    out.message(WARNING, MUTED)
