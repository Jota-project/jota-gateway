"""`jota-gw clients …`: gestión de clientes por la API /admin/clients."""

from typing import Any

import click

from src.cli import api, refs
from src.cli.output import Out

COLUMNS = [
    ("NOMBRE", "name"),
    ("ID", "short_id"),
    ("TIPO", "client_type"),
    ("ACTIVO", "is_active"),
    ("AGENTE", "default_agent"),
    ("FP", "key_fp"),
]


def _out(ctx: click.Context) -> Out:
    return Out(ctx.find_root().obj["json"])


def _find(client: api.Api, ref: str) -> dict[str, Any]:
    return refs.resolve(client.request("GET", "/admin/clients"), ref)


@click.group()
def clients() -> None:
    """Gestiona los clientes (por id, prefijo de id o nombre)."""


@clients.command("list")
@click.pass_context
def list_(ctx: click.Context) -> None:
    """Lista los clientes."""
    out = _out(ctx)
    rows = api.from_context(ctx).request("GET", "/admin/clients")
    if out.as_json:
        out.json(rows)
    elif not rows:
        out.message("Sin clientes. Crea uno con `jota-gw clients add --name …`.")
    else:
        out.table(COLUMNS, [{**r, "short_id": r["id"][:8]} for r in rows])


@clients.command()
@click.argument("ref")
@click.pass_context
def show(ctx: click.Context, ref: str) -> None:
    """Enseña todos los datos de un cliente, salvo su clave."""
    out = _out(ctx)
    client = api.from_context(ctx)
    data = client.request("GET", f"/admin/clients/{_find(client, ref)['id']}")
    if out.as_json:
        out.json(data)
    else:
        out.pairs(list(data.items()))
