"""
menu.py
~~~~~~~
Menú interactivo de `jota-gw` sin argumentos (decisión de Sito, 2026-11-01). Cada entrada
ejecuta el comando de siempre, así que no duplica lógica. `questionary` se importa solo aquí
y solo al abrirlo.
"""

import sys
from typing import Any

import click

from jota_gateway.cli import api
from jota_gateway.cli.output import ACCENT, INFO, MUTED

MAIN = [
    ("Estado", "status", ["status"]),
    ("Diagnóstico", "doctor", ["doctor"]),
    ("Clientes", "clients", None),
    ("Configuración", "config", ["config", "show"]),
    ("Salir", "exit", None),
]
CLIENT_ACTIONS = [
    ("Listar", "list"),
    ("Ver", "show"),
    ("Crear", "add"),
    ("Editar un campo", "edit"),
    ("Activar", "enable"),
    ("Desactivar", "disable"),
    ("Rotar la clave", "rotate-key"),
    ("Borrar", "remove"),
    ("Volver", "back"),
]
CLIENT_TYPES = ["ha", "esp32", "web", "app"]


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _style() -> Any:
    import questionary

    return questionary.Style([
        ("qmark", f"fg:{ACCENT}"),
        ("question", "bold"),
        ("pointer", f"fg:{ACCENT} bold"),
        ("highlighted", f"fg:{ACCENT}"),
        ("answer", f"fg:{INFO}"),
        ("instruction", f"fg:{MUTED}"),
    ])


def _select(message: str, options: list[tuple[str, str]]) -> str | None:
    import questionary

    choices = [questionary.Choice(title, value=value) for title, value in options]
    answer: str | None = questionary.select(message, choices=choices, style=_style()).ask()
    return answer


def _text(message: str) -> str | None:
    import questionary

    answer: str | None = questionary.text(message, style=_style()).ask()
    return answer


def _invoke(ctx: click.Context, argv: list[str]) -> None:
    from jota_gateway.cli.app import cli

    obj = ctx.find_root().obj
    base = ["--url", obj["url"], *(["--token", obj["token"]] if obj["token"] else [])]
    try:
        cli.main(args=[*base, *(["--yes"] if obj["yes"] else []), *argv],
                 prog_name="jota-gw", standalone_mode=False)
    except click.ClickException as e:
        e.show()
    except click.Abort:
        click.echo("Cancelado.", err=True)


def _pick_client(ctx: click.Context) -> dict[str, Any] | None:
    try:
        rows = api.from_context(ctx).request("GET", "/admin/clients")
    except api.ApiError as e:
        e.show()
        return None
    if not rows:
        click.echo("Sin clientes todavía.")
        return None
    chosen = _select("¿Qué cliente?", [(f"{r['name']}  ({r['id'][:8]})", r["id"]) for r in rows])
    return next((r for r in rows if r["id"] == chosen), None)


def _clients_argv(ctx: click.Context) -> list[str] | None:
    from jota_gateway.cli.clients import READ_ONLY

    action = _select("Clientes", CLIENT_ACTIONS)
    if action in (None, "back"):
        return None
    if action == "list":
        return ["clients", "list"]
    if action == "add":
        name = _text("Nombre del cliente")
        if not name:
            return None
        kind = _select("Tipo", [*((t, t) for t in CLIENT_TYPES), ("Sin tipo", "")])
        return ["clients", "add", "--name", name, *(["--type", kind] if kind else [])]
    client = _pick_client(ctx)
    if client is None:
        return None
    if action == "edit":
        fields = sorted(set(client) - READ_ONLY)
        field = _select("¿Qué campo?", [(f, f) for f in fields])
        value = _text(f"Valor nuevo de {field}") if field else None
        if value is None:
            return None
        return ["clients", "edit", client["id"], "--set", f"{field}={value}"]
    return ["clients", action, client["id"]]


def run_menu(ctx: click.Context) -> None:
    """Hasta «Salir» o Ctrl+C (questionary devuelve None)."""
    argv_for = {value: argv for _, value, argv in MAIN}
    while True:
        choice = _select("¿Qué quieres hacer?", [(title, value) for title, value, _ in MAIN])
        if choice in (None, "exit"):
            return
        argv = _clients_argv(ctx) if choice == "clients" else argv_for[choice]
        if argv:
            _invoke(ctx, argv)
