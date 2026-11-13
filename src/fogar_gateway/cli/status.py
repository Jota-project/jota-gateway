"""`fogar-gw status`: estado del gateway y de sus servicios (GET /admin/state)."""

from typing import Any

import click

from fogar_gateway.cli import api
from fogar_gateway.cli.output import Out


def _uptime(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86_400:
        return f"{s // 3600} h {s % 3600 // 60} min"
    return f"{s // 86_400} d {s % 86_400 // 3600} h"


def _access(orch: dict[str, Any]) -> str | None:
    """Acceso a OpenClaw (M19); None con un gateway que no lo da."""
    pairing = orch.get("pairing")
    if pairing in (None, "unknown"):
        return None
    if pairing == "pending":
        rid = orch.get("pairing_request_id") or "--latest"
        return f"pendiente de aprobar: `openclaw devices approve {rid}` en la máquina de OpenClaw"
    how = {"shared_token": "token compartido", "device_token": "token de dispositivo"}.get(
        str(orch.get("auth_method")), "?")
    paired = " · emparejado" if pairing == "paired" else ""
    return f"{'admin' if orch.get('admin') else 'sin admin'} · {how}{paired}"


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
    orch = services.get("orchestrator", {})
    access = _access(orch)
    out.pairs(
        [
            ("gateway", f"en marcha · v{gw.get('version', '?')}"),
            ("encendido", "?" if uptime is None else _uptime(uptime)),
            ("orquestador", _service(orch)),
            *([("acceso OpenClaw", access)] if access else []),
            ("STT", _service(services.get("stt", {}))),
            ("TTS", _service(services.get("tts", {}))),
            ("sesiones", state.get("totals", {}).get("sessions_active", "?")),
        ]
    )
