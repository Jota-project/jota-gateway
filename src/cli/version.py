"""`jota-gw version`: versión de la CLI y, si responde, la del gateway."""

import click

from src._version import __version__
from src.cli import api
from src.cli.output import Out


@click.command()
@click.pass_context
def version(ctx: click.Context) -> None:
    """Versión de la CLI y del gateway."""
    out = Out(ctx.find_root().obj["json"])
    try:
        gateway = api.from_context(ctx).request("GET", "/admin/state")["gateway"].get("version")
    except api.ApiError:
        gateway = None  # sin gateway o sin token: basta con la versión de la CLI
    if out.as_json:
        out.json({"cli": __version__, "gateway": gateway})
        return
    out.pairs([("cli", __version__), ("gateway", gateway or "no responde")])
