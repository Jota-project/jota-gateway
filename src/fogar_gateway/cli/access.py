"""`fogar-gw access`: desde dónde se llega al gateway, verificado y con una vía recomendada."""

import json
import urllib.request
from typing import Any

import click

from fogar_gateway.cli.output import ACCENT, MUTED, Out

HTTPS_HINT = ("Para hablar con Fogar desde el navegador hace falta HTTPS: activa Tailscale o tu "
              "dominio con `fogar-gw access setup`")


def gateway_instance_id(url: str) -> str | None:
    """El instance_id del gateway en marcha, o None si no responde."""
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/.well-known/fogar-gateway", timeout=2) as r:
            data = json.load(r)
        iid = data.get("instance_id") if isinstance(data, dict) else None
        return iid if isinstance(iid, str) else None
    except (OSError, ValueError):  # URLError, timeout y JSON ilegible: para la CLI, «no responde»
        return None


def _status_text(r: Any) -> str:
    return {
        "ok": "verificada",
        "protegida": "protegida con Cloudflare Access: no la he podido comprobar",
        "inalcanzable": f"no responde ({r.detail})",
        "otro_gateway": "llega a otro gateway",
    }.get(r.status, "sin comprobar")


def show_routes(out: Out, routes: list[Any]) -> None:
    rec = next((r for r in routes if r.recommended), None)
    if rec:
        out.message(f"Recomendada: {rec.url}  ({_status_text(rec)})", ACCENT)
    for r in routes:
        if r is not rec:
            out.message(f"{r.url}  ({_status_text(r)})", MUTED)
    if rec and not rec.https:
        out.message(HTTPS_HINT, MUTED)


@click.group(invoke_without_command=True)
@click.pass_context
def access(ctx: click.Context) -> None:
    """Desde dónde se llega al gateway."""
    if ctx.invoked_subcommand is not None:
        return
    from fogar_gateway import access as acc
    from fogar_gateway.cli import config as cfg
    from fogar_gateway.cli import serve

    out = Out(ctx.find_root().obj["json"])
    settings, _ = cfg._load_or_fail(out)
    iid = gateway_instance_id(ctx.find_root().obj["url"])
    if iid is None:
        out.error("El gateway no responde: no puedo comprobar las vías")
    routes = acc.collect(settings.GATEWAY_HOST, serve.PORT, acc.configured_public_urls(), iid)
    if out.as_json:
        out.json({"docker": acc.in_docker(), "routes": [r.as_dict() for r in routes]})
    else:
        show_routes(out, routes)
