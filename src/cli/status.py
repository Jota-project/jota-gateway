"""`jota-gw status`: estado del gateway y de sus servicios (GET /admin/state)."""

from typing import Any

import click

from src.cli import api
from src.cli.output import Out


def _uptime(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86_400:
        return f"{s // 3600} h {s % 3600 // 60} min"
    return f"{s // 86_400} d {s % 86_400 // 3600} h"


def _service(svc: dict[str, Any]) -> str:
    text = " · ".join(str(v) for v in (svc.get("provider"), svc.get("state", "?")) if v)
    return f"{text} ({svc['last_error']})" if svc.get("last_error") else text


@click.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Estado del gateway y sus servicios."""
    out = Out(ctx.find_root().obj["json"])
    state = api.from_context(ctx).request("GET", "/admin/state")
    if out.as_json:
        out.json(state)
        return
    # .get con valores por defecto: un gateway de otra versión puede traer otra forma.
    gw, services = state.get("gateway", {}), state.get("services", {})
    uptime = gw.get("uptime_s")
    out.pairs(
        [
            ("gateway", f"en marcha · v{gw.get('version', '?')}"),
            ("encendido", "?" if uptime is None else _uptime(uptime)),
            ("orquestador", _service(services.get("orchestrator", {}))),
            ("STT", _service(services.get("stt", {}))),
            ("TTS", _service(services.get("tts", {}))),
            ("sesiones", state.get("totals", {}).get("sessions_active", "?")),
        ]
    )
