"""
output.py
~~~~~~~~~
Toda la salida de la CLI. Con terminal y color, tema Jota Dusk con `rich` (importado
solo aquí y solo al pintar); con --json, NO_COLOR o sin terminal, texto limpio.
"""

import json
import os
import sys
from typing import Any

import click

# Tema Jota Dusk (spec 2026-11-01).
TEXT = "#ddd6d0"
MUTED = "#bab2c4"
ACCENT = "#c3b7e3"
INFO = "#6e8cc4"
ERROR = "#e08a8a"


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
            console.print(f"[{MUTED}]{escape(key.ljust(width))}[/]  [{TEXT}]{escape(fmt(value))}[/]")

    def message(self, text: str, color: str = TEXT) -> None:
        if self.styled:
            self._console().print(text, style=color, markup=False)
        else:
            click.echo(text)

    def error(self, text: str) -> None:
        if self.styled:
            self._console(stderr=True).print(text, style=ERROR, markup=False)
        else:
            click.echo(text, err=True)
