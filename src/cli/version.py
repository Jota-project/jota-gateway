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
    answered = True
    try:
        state = api.from_context(ctx).request("GET", "/admin/state")
        gateway = (state.get("gateway") or {}).get("version")
    except api.ApiError:
        answered, gateway = False, None  # sin gateway o sin token: basta con la de la CLI
    if out.as_json:
        out.json({"cli": __version__, "gateway": gateway})
        return
    shown = gateway or ("sin versión (anterior a 1.23)" if answered else "no responde")
    out.pairs([("cli", __version__), ("gateway", shown)])
