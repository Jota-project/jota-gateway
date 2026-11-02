"""
app.py
~~~~~~
Grupo raíz de `jota-gw`. Cada subcomando se importa solo al usarlo: `--help` no
carga ni la API ni `rich` (objetivo < 150 ms, #293).
"""

import importlib

import click

DEFAULT_URL = "http://127.0.0.1:8004"

# nombre -> ("módulo:atributo", ayuda corta). La ayuda va aquí para no importar el módulo.
COMMANDS: dict[str, tuple[str, str]] = {
    "clients": ("src.cli.clients:clients", "Gestiona los clientes."),
    "config": ("src.cli.config:config", "Configuración efectiva (solo lectura)."),
    "status": ("src.cli.status:status", "Estado del gateway y sus servicios."),
    "version": ("src.cli.version:version", "Versión de la CLI y del gateway."),
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


@click.group(cls=LazyGroup, invoke_without_command=True)
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
        # shortcut: sin argumentos, la ayuda; el menú interactivo llega en 14b.
        click.echo(ctx.get_help())
