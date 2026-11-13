"""`fogar-gw access`: desde dónde se llega al gateway, verificado y con una vía recomendada."""

import getpass
import json
import re
import shutil
import subprocess
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


def free_serve_port(serve: dict[str, Any] | None) -> int | None:
    """Puerto HTTPS libre en `tailscale serve` (443, si no 8443); None si ambos están ocupados."""
    tcp = (serve or {}).get("TCP")
    taken = tcp if isinstance(tcp, dict) else {}
    return next((p for p in (443, 8443) if str(p) not in taken), None)


def enable_serve(port: int, gw_port: int, exe: str = "tailscale") -> tuple[str, str]:
    """`tailscale serve --bg` hacia el gateway: ("ok"|"permiso"|"https_off"|"error", detalle). No lanza."""
    path = shutil.which(exe)
    if path is None:
        return "error", "FileNotFoundError"
    cmd = [path, "serve", "--bg", f"--https={port}", f"http://127.0.0.1:{gw_port}"]
    try:
        r = subprocess.run(cmd, timeout=15, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError) as e:  # TimeoutExpired incluido: nunca cuelga
        return "error", type(e).__name__
    if r.returncode == 0:
        return "ok", ""
    out = f"{r.stdout}\n{r.stderr}"
    if "Access denied" in out or "--operator" in out:
        return "permiso", ""
    if m := re.search(r"https://login\.tailscale\.com/\S+", out):
        return "https_off", m.group(0)
    return "error", f"exit {r.returncode}"


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


def _tailscale_url(status: dict[str, Any], port: int) -> str:
    me = status.get("Self")
    dns = str(me.get("DNSName", "")).rstrip(".") if isinstance(me, dict) else ""
    return f"https://{dns or '<tu equipo en la tailnet>'}{'' if port == 443 else f':{port}'}/"


def _tailscale(out: Out, interactive: bool, gw_port: int) -> None:
    """Activa `tailscale serve` hacia el gateway. Sin terminal (`--answers`) no pregunta: lo
    que pida al usuario (instalar, permiso, HTTPS) solo se explica y se sigue. Nunca pisa un
    handler existente ni ejecuta sudo sin que se elija."""
    from fogar_gateway import access as acc
    from fogar_gateway.cli.onboard import _ask, _choose

    while (status := acc.tailscale_status()) is None or status.get("BackendState") != "Running":
        out.message("Tailscale no está instalado o no está conectado. Instálalo y conéctalo:")
        out.message("  curl -fsSL https://tailscale.com/install.sh | sh", ACCENT)
        out.message("  sudo tailscale up", ACCENT)
        if not interactive or _choose("¿Sigo?", [("Ya está: vuelve a mirar", "retry"),
                                                 ("Seguir sin Tailscale", "skip")]) == "skip":
            return
    serve = acc.serve_status()
    if (current := acc.serve_https_port(serve, gw_port)) is not None:
        out.message(f"Ya está: {_tailscale_url(status, current)}")
        return
    if interactive and not _ask("confirm", "¿Activo HTTPS con `tailscale serve`?", default=True):
        return
    port = free_serve_port(serve)
    if port is None:
        out.message("443 y 8443 ya sirven otra cosa: no lo toco")
        return
    sudo_done = False
    while True:
        result, detail = enable_serve(port, gw_port)
        if result == "ok":
            out.message(f"Listo: {_tailscale_url(status, port)}")
            return
        if result == "permiso":
            out.message("Tailscale necesita permiso para que tu usuario lo configure: "
                        "`sudo tailscale set --operator=$USER`")
            if not interactive or sudo_done or _choose(
                    "¿Lo ejecuto?", [("Ejecutarlo ahora", "sudo"), ("Saltar", "skip")]) == "skip":
                return
            sudo_done = True  # un solo reintento tras el sudo
            try:
                subprocess.run(["sudo", "tailscale", "set", f"--operator={getpass.getuser()}"],
                               check=False)
            except OSError as e:  # sin sudo: se explica y se sigue
                out.error(f"No he podido ejecutar sudo ({type(e).__name__})")
                return
        elif result == "https_off":
            out.message(f"Activa HTTPS en tu tailnet: {detail}")
            if not interactive or _choose("¿Sigo?", [("Ya lo he activado: reintentar", "retry"),
                                                     ("Saltar", "skip")]) == "skip":
                return
        else:
            out.error(f"No he podido activar `tailscale serve` ({detail})")
            return


def _valid_urls(value: str) -> bool | str:
    from fogar_gateway.access import parse_public_urls

    try:
        return bool(parse_public_urls(value)) or "Es obligatorio."
    except ValueError as e:  # el mensaje fijo de parse_public_urls, sin eco del valor
        return str(e)


def _domain(out: Out, urls: list[str] | None) -> None:
    """Guarda URLs propias en PUBLIC_URLS, unidas a las que ya hay sin duplicar."""
    from fogar_gateway import access as acc
    from fogar_gateway.cli import config as cfg
    from fogar_gateway.cli import serve
    from fogar_gateway.cli.onboard import _ask

    if urls is None:
        raw = _ask("text", "Tu dirección HTTPS (p. ej. https://fogar.example.com)",
                   validate=_valid_urls)
        urls = acc.parse_public_urls(raw)
    out.message(f"Haz que apunte a http://localhost:{serve.PORT}")
    merged = list(dict.fromkeys(acc.configured_public_urls() + urls))
    cfg._apply(out, "PUBLIC_URLS", ",".join(merged))


def access_step(out: Out, answers: Any | None, settings: Any) -> None:
    """«¿Cómo vas a entrar a Fogar?»: casa (siempre), Tailscale y/o dominio propio.
    `answers` es el `access` de `--answers` (ya validado) o None (pregunta)."""
    from fogar_gateway.access import parse_public_urls
    from fogar_gateway.cli import serve

    if answers is not None:
        if answers.tailscale_serve:
            _tailscale(out, False, serve.PORT)
        if answers.public_urls:
            _domain(out, parse_public_urls(",".join(answers.public_urls)))
        return
    import questionary

    from fogar_gateway.cli.onboard import _ask

    choices = [questionary.Choice("Desde casa (red local)", value="lan", checked=True,
                                  disabled="siempre"),
               questionary.Choice("Con Tailscale", value="tailscale"),
               questionary.Choice("Con mi propio dominio", value="domain")]
    chosen = _ask("checkbox", "¿Cómo vas a entrar a Fogar?", choices=choices)
    if "tailscale" in chosen:
        _tailscale(out, True, serve.PORT)
    if "domain" in chosen:
        _domain(out, None)


def _list(ctx: click.Context, out: Out, settings: Any) -> None:
    from fogar_gateway import access as acc
    from fogar_gateway.cli import serve

    iid = gateway_instance_id(ctx.find_root().obj["url"])
    if iid is None:
        out.error("El gateway no responde: no puedo comprobar las vías")
    routes = acc.collect(settings.GATEWAY_HOST, serve.PORT, acc.configured_public_urls(), iid)
    if out.as_json:
        out.json({"docker": acc.in_docker(), "routes": [r.as_dict() for r in routes]})
    else:
        show_routes(out, routes)


@click.group(invoke_without_command=True)
@click.pass_context
def access(ctx: click.Context) -> None:
    """Desde dónde se llega al gateway."""
    if ctx.invoked_subcommand is not None:
        return
    from fogar_gateway.cli import config as cfg

    out = Out(ctx.find_root().obj["json"])
    settings, _ = cfg._load_or_fail(out)
    _list(ctx, out, settings)


@access.command()
@click.pass_context
def setup(ctx: click.Context) -> None:
    """Elige cómo entrar: Tailscale o tu dominio."""
    from fogar_gateway.cli import api, output
    from fogar_gateway.cli import config as cfg

    out = Out(ctx.find_root().obj["json"])
    if not output._stdin_isatty():
        raise api.ApiError("`fogar-gw access setup` pregunta y aquí no hay terminal: "
                           "ejecútalo en una.", 1)
    settings, _ = cfg._load_or_fail(out)
    access_step(out, None, settings)
    _list(ctx, out, settings)
