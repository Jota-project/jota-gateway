"""
app.py
~~~~~~
Grupo raíz de `fogar-gw`. Cada subcomando se importa solo al usarlo: `--help` no
carga ni la API ni `rich` (objetivo < 150 ms, #293).
"""

import importlib
import os
from pathlib import Path

import click

DEFAULT_URL = "http://127.0.0.1:8004"

# nombre -> ("módulo:atributo", ayuda corta). La ayuda va aquí para no importar el módulo.
COMMANDS: dict[str, tuple[str, str]] = {
    "clients": ("fogar_gateway.cli.clients:clients", "Gestiona los clientes."),
    "config": ("fogar_gateway.cli.config:config", "Configuración: ver, validar y cambiar."),
    "doctor": ("fogar_gateway.cli.doctor:doctor", "Diagnostica la instalación."),
    "init": ("fogar_gateway.cli.init:init", "Prepara una instalación nueva."),
    "install": ("fogar_gateway.cli.service:install", "Instala y arranca el servicio."),
    "logs": ("fogar_gateway.cli.logs:logs", "Registros del servicio."),
    "migrate": ("fogar_gateway.cli.migrate:migrate", "Copia una instalación Docker a la nativa."),
    "onboard": ("fogar_gateway.cli.onboard:onboard", "Asistente: OpenClaw, voz, servicio y primer cliente."),
    "pair": ("fogar_gateway.cli.pair:pair", "Empareja un cliente con un código."),
    "serve": ("fogar_gateway.cli.serve:serve", "Arranca el gateway."),
    "start": ("fogar_gateway.cli.service:start", "Arranca el servicio."),
    "status": ("fogar_gateway.cli.status:status", "Estado del gateway y sus servicios."),
    "stop": ("fogar_gateway.cli.service:stop", "Para el servicio."),
    "restart": ("fogar_gateway.cli.service:restart", "Reinicia el servicio."),
    "uninstall": ("fogar_gateway.cli.service:uninstall", "Quita el servicio; no toca los datos."),
    "version": ("fogar_gateway.cli.version:version", "Versión de la CLI y del gateway."),
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
        os.environ["FOGAR_GATEWAY_HOME"] = str(Path(value).expanduser().absolute())


@click.group(cls=LazyGroup, invoke_without_command=True)
@click.option("--home", metavar="DIR", callback=_set_home, is_eager=True, expose_value=False,
              help="Directorio base (por defecto FOGAR_GATEWAY_HOME o ~/.fogar-gateway).")
@click.option("--url", envvar="FOGAR_GW_URL", default=DEFAULT_URL, show_default=True,
              help="Dirección del gateway.")
@click.option("--token", envvar="FOGAR_GW_TOKEN", help="Token de administración.")
@click.option("--json", "as_json", is_flag=True, help="Salida JSON, sin estilo.")
@click.option("--yes", "-y", is_flag=True, help="No preguntar en lo destructivo.")
@click.pass_context
def cli(ctx: click.Context, url: str, token: str | None, as_json: bool, yes: bool) -> None:
    """Administra el gateway de Fogar."""
    ctx.obj = {"url": url, "token": token, "json": as_json, "yes": yes}
    if ctx.invoked_subcommand is None:
        from fogar_gateway.cli import menu

        # Menú solo en una terminal interactiva y sin --json; si no (scripts, tuberías), ayuda.
        if not as_json and menu.interactive():
            menu.run_menu(ctx)
        else:
            click.echo(ctx.get_help())
