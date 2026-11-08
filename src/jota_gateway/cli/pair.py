"""`jota-gw pair`: abre un código de emparejamiento y espera a que un cliente lo use (#317).

El cliente escribe el código en su pantalla de emparejamiento y llama a `POST /pair`; aquí se fija
cómo se comportará (agente, voz…), igual que con `clients add`.
"""

import time
from datetime import datetime
from typing import Any

import click

from jota_gateway.cli import api
from jota_gateway.cli.clients import set_values
from jota_gateway.cli.output import ACCENT, MUTED, Out

POLL_S = 1.0


def _sleep(seconds: float) -> None:
    time.sleep(seconds)  # aparte para que los tests no esperen


@click.command()
@click.option("--name", help="Nombre del cliente. Si no se da, lo propone el cliente.")
@click.option("--type", "client_type", help="Tipo: ha, esp32, web, app o screen.")
@click.option("--agent", help="Agente por defecto.")
@click.option("--allowed-agent", "allowed", multiple=True, help="Agente permitido (repetible).")
@click.option("--set", "sets", multiple=True, metavar="CAMPO=TEXTO|CAMPO:=JSON",
              help="Cualquier otro campo del cliente (voz, turno…). Con := el valor va como JSON.")
@click.option("--no-wait", is_flag=True, help="Enseña el código y termina, sin esperar.")
@click.pass_context
def pair(
    ctx: click.Context,
    name: str | None,
    client_type: str | None,
    agent: str | None,
    allowed: tuple[str, ...],
    sets: tuple[str, ...],
    no_wait: bool,
) -> None:
    """Abre un código para que un cliente se dé de alta solo."""
    from jota_gateway.models.admin_schemas import ClientCreate

    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if client_type:
        body["client_type"] = client_type
    if agent:
        body["default_agent"] = agent
    if allowed:
        body["allowed_agents"] = list(allowed)
    body.update(set_values(sets, set(ClientCreate.model_fields) - {"client_key"}))

    out = Out(ctx.find_root().obj["json"])
    client = api.from_context(ctx)
    item = client.request("POST", "/admin/pairing", body)
    code = item["code"]
    expires = datetime.fromisoformat(item["expires_at"]).astimezone().strftime("%H:%M")
    if out.as_json:
        out.json({"id": item["id"], "code": code, "expires_at": item["expires_at"]})
        return
    out.message(f"{code[:3]} {code[3:]}", ACCENT)
    if no_wait:
        out.message(f"Caduca a las {expires}.", MUTED)
        return
    gateway = client.request("GET", "/.well-known/jota-gateway")["name"]
    out.message(
        f"Escríbelo en el cliente para emparejarlo con «{gateway}». Caduca a las {expires}; "
        "Ctrl+C lo anula.",
        MUTED,
    )
    try:
        while True:
            _sleep(POLL_S)
            state = client.request("GET", f"/admin/pairing/{item['id']}")
            if state["state"] == "used":
                out.message(f"Cliente «{state['client_name']}» emparejado (id {state['client_id'][:8]}).")
                return
            if state["state"] != "open":
                verb = "caducado" if state["state"] == "expired" else "sido anulado"
                raise api.ApiError(f"El código ha {verb}. Abre otro con `jota-gw pair`.")
    except KeyboardInterrupt:
        client.request("DELETE", f"/admin/pairing/{item['id']}", accept=(404,))
        raise api.ApiError("Código anulado.") from None
