"""
app.py
~~~~~~
Grupo raíz de `jota-gw`. Cada subcomando se importa solo al usarlo: `--help` no
carga ni la API ni `rich` (objetivo < 150 ms, #293).
"""

import importlib
import os
from pathlib import Path

import click

DEFAULT_URL = "http://127.0.0.1:8004"

# nombre -> ("módulo:atributo", ayuda corta). La ayuda va aquí para no importar el módulo.
COMMANDS: dict[str, tuple[str, str]] = {
    "clients": ("jota_gateway.cli.clients:clients", "Gestiona los clientes."),
    "config": ("jota_gateway.cli.config:config", "Configuración: ver, validar y cambiar."),
    "doctor": ("jota_gateway.cli.doctor:doctor", "Diagnostica la instalación."),
    "init": ("jota_gateway.cli.init:init", "Prepara una instalación nueva."),
    "install": ("jota_gateway.cli.service:install", "Instala y arranca el servicio."),
    "logs": ("jota_gateway.cli.logs:logs", "Registros del servicio."),
    "migrate": ("jota_gateway.cli.migrate:migrate", "Copia una instalación Docker a la nativa."),
    "onboard": ("jota_gateway.cli.onboard:onboard", "Asistente: OpenClaw, voz, servicio y primer cliente."),
    "serve": ("jota_gateway.cli.serve:serve", "Arranca el gateway."),
    "start": ("jota_gateway.cli.service:start", "Arranca el servicio."),
    "status": ("jota_gateway.cli.status:status", "Estado del gateway y sus servicios."),
    "stop": ("jota_gateway.cli.service:stop", "Para el servicio."),
    "restart": ("jota_gateway.cli.service:restart", "Reinicia el servicio."),
    "uninstall": ("jota_gateway.cli.service:uninstall", "Quita el servicio; no toca los datos."),
    "version": ("jota_gateway.cli.version:version", "Versión de la CLI y del gateway."),
}


class LazyGroup(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(COMMANDS)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        if cmd_name not in COMMANDS:
            return None
        module, attr = COMMANDS[cmd_name][0].split(":")
        command: click.Command = getattr(importlib.import_module(module), attr)
        return command

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        rows = [(name, COMMANDS[name][1]) for name in self.list_commands(ctx)]
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)


def _set_home(ctx: click.Context, param: click.Parameter, value: str | None) -> None:
    # Antes de importar nada que lea la configuración: Settings lee <base>/config.env al importarse.
    if value:
        os.environ["JOTA_GATEWAY_HOME"] = str(Path(value).expanduser().absolute())


@click.group(cls=LazyGroup, invoke_without_command=True)
@click.option("--home", metavar="DIR", callback=_set_home, is_eager=True, expose_value=False,
              help="Directorio base (por defecto JOTA_GATEWAY_HOME o ~/.jota-gateway).")
@click.option("--url", envvar="JOTA_GW_URL", default=DEFAULT_URL, show_default=True,
              help="Dirección del gateway.")
@click.option("--token", envvar="JOTA_GW_TOKEN", help="Token de administración.")
@click.option("--json", "as_json", is_flag=True, help="Salida JSON, sin estilo.")
@click.option("--yes", "-y", is_flag=True, help="No preguntar en lo destructivo.")
@click.pass_context
def cli(ctx: click.Context, url: str, token: str | None, as_json: bool, yes: bool) -> None:
    """Administra el gateway de Jota."""
    ctx.obj = {"url": url, "token": token, "json": as_json, "yes": yes}
    if ctx.invoked_subcommand is None:
        from jota_gateway.cli import menu

        # Menú solo en una terminal interactiva y sin --json; si no (scripts, tuberías), ayuda.
        if not as_json and menu.interactive():
            menu.run_menu(ctx)
        else:
            click.echo(ctx.get_help())
