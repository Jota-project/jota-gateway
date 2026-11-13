"""
output.py
~~~~~~~~~
Toda la salida de la CLI. Con terminal y color, la paleta de fogar-brand (`marca/paleta.css`)
con `rich` (importado solo aquí y solo al pintar); con --json, NO_COLOR o sin terminal, texto limpio.

Cada color dice siempre lo mismo (reglas de la marca):
- el texto va sin color (vale en terminales claras y oscuras) y lo que se copia o se teclea, en negrita;
- MUTED (hueso-tenue): pistas y metadatos;
- BRASA (llama): solo lo que pasa ahora (el paso en curso, la pregunta, el spinner); nunca rellena texto;
- los estados, planos y con icono: OK ■ (correcto), WARN ◆ (degradado), ERROR ▲ (error).
"""

import json
import os
import sys
from typing import Any

import click

from fogar_gateway.cli.api import ApiError

TEXT = ""
MUTED = "#8b969f"
ACCENT = "bold"
BRASA = "#ee6a2c"
OK = "#78b8f0"
WARN = "#e6df7a"
ERROR = "#ff8db4"
INFO = OK  # nombre antiguo: el color de lo ya resuelto


def _isatty() -> bool:
    return sys.stdout.isatty()


def fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "—"
    return str(value)


class Out:
    def __init__(self, as_json: bool) -> None:
        self.as_json = as_json
        self.styled = not as_json and not os.environ.get("NO_COLOR") and _isatty()

    def _console(self, stderr: bool = False) -> Any:
        from rich.console import Console

        return Console(
            stderr=stderr, force_terminal=True, color_system="truecolor", highlight=False
        )

    def json(self, data: Any) -> None:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))

    def table(self, columns: list[tuple[str, str]], rows: list[dict[str, Any]]) -> None:
        cells = [[fmt(row.get(key)) for _, key in columns] for row in rows]
        if not self.styled:
            headers = [h for h, _ in columns]
            widths = [max(len(c) for c in col) for col in zip(headers, *cells, strict=False)]
            for line in [headers, *cells]:
                click.echo("  ".join(c.ljust(w) for c, w in zip(line, widths)).rstrip())
            return
        from rich.markup import escape
        from rich.table import Table

        table = Table(box=None, header_style=f"bold {MUTED}", style=TEXT, pad_edge=False)
        for i, (header, _) in enumerate(columns):
            table.add_column(header, style=ACCENT if i == 0 else TEXT)
        for line in cells:
            table.add_row(*(escape(c) for c in line))  # un nombre con [ ] no es markup
        self._console().print(table)

    def pairs(self, items: list[tuple[str, Any]]) -> None:
        width = max((len(k) for k, _ in items), default=0)
        if not self.styled:
            for key, value in items:
                click.echo(f"{key.ljust(width)}  {fmt(value)}")
            return
        from rich.markup import escape

        console = self._console()
        for key, value in items:
            console.print(f"[{MUTED}]{escape(key.ljust(width))}[/]  {escape(fmt(value))}")

    def message(self, text: str, color: str = TEXT) -> None:
        if self.styled:
            self._console().print(text, style=color, markup=False)
        else:
            click.echo(text)

    def error(self, text: str) -> None:
        if self.styled:
            self._console(stderr=True).print(f"▲ {text}", style=ERROR, markup=False)
        else:
            click.echo(text, err=True)

    # Los pasos del asistente. Sin estilo, `step` no pinta nada y el resto es `message`: la
    # salida de siempre (--answers, el CI, NO_COLOR).

    def step(self, name: str) -> None:
        """Abre un paso: «● Nombre», con la brasa en el punto (lo que pasa ahora)."""
        if self.styled:
            from rich.text import Text

            self._console().print(Text.assemble("\n", ("● ", BRASA), (name, "bold")))

    def ok(self, text: str) -> None:
        """Lo resuelto en un paso: «■ texto» en el color de correcto."""
        self._mark("■", OK, text)

    def warn(self, text: str) -> None:
        self._mark("◆", WARN, text)

    def note(self, text: str) -> None:
        """Una pista bajo el paso, en tenue."""
        if self.styled:
            self._console().print(f"  {text}", style=MUTED, markup=False)
        else:
            click.echo(text)

    def _mark(self, icon: str, color: str, text: str) -> None:
        if self.styled:
            from rich.text import Text

            self._console().print(Text.assemble("  ", (f"{icon} ", color), text))
        else:
            click.echo(text)


def _stdin_isatty() -> bool:
    return sys.stdin.isatty()


def confirm(ctx: click.Context, question: str) -> None:
    """Pregunta antes de lo destructivo; `--yes` la salta y sin terminal sale con 2."""
    if ctx.find_root().obj["yes"]:
        return
    if not _stdin_isatty():
        raise ApiError(f"{question} Sin terminal para preguntar: repite con --yes.", 2)
    click.confirm(question, abort=True, err=True)
