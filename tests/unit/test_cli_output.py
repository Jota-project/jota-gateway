"""Salida de la CLI: tema, tablas, pares y JSON en un solo sitio (M14a, #293)."""

import json

import pytest

from fogar_gateway.cli import output


@pytest.fixture()
def tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(output, "_isatty", lambda: True)


def test_plain_when_not_a_terminal(monkeypatch):
    monkeypatch.setattr(output, "_isatty", lambda: False)
    assert output.Out(as_json=False).styled is False


def test_plain_with_no_color(tty, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert output.Out(as_json=False).styled is False


def test_plain_with_json(tty):
    assert output.Out(as_json=True).styled is False


def test_styled_on_a_terminal(tty):
    assert output.Out(as_json=False).styled is True


def test_plain_table_is_aligned(monkeypatch, capsys):
    monkeypatch.setattr(output, "_isatty", lambda: False)
    rows = [{"name": "Fogar-TV", "id": "ab12"}, {"name": "HA", "id": "cd34"}]
    output.Out(as_json=False).table([("NOMBRE", "name"), ("ID", "id")], rows)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["NOMBRE", "ID"]
    assert lines[1].split() == ["Fogar-TV", "ab12"]
    assert lines[0].index("ID") == lines[1].index("ab12") == lines[2].index("cd34")


def test_plain_pairs(monkeypatch, capsys):
    monkeypatch.setattr(output, "_isatty", lambda: False)
    output.Out(as_json=False).pairs([("nombre", "A"), ("activo", True), ("agente", None)])
    out = capsys.readouterr().out
    assert "nombre" in out and "A" in out and "sí" in out and "—" in out


def test_json_is_valid_and_unstyled(capsys):
    output.Out(as_json=True).json({"a": "ñ", "b": [1, 2]})
    assert json.loads(capsys.readouterr().out) == {"a": "ñ", "b": [1, 2]}


def test_styled_table_uses_theme_colors(tty, capsys):
    output.Out(as_json=False).table([("NOMBRE", "name")], [{"name": "Fogar"}])
    out = capsys.readouterr().out
    assert "Fogar" in out and "\x1b[" in out


def test_error_goes_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(output, "_isatty", lambda: False)
    output.Out(as_json=False).error("mal")
    captured = capsys.readouterr()
    assert "mal" in captured.err and captured.out == ""


@pytest.mark.parametrize("name", ["Sala [cocina]", "raro [/]", "[bold]x"])
def test_styled_values_are_not_markup(tty, capsys, name):
    out = output.Out(as_json=False)
    out.pairs([("nombre", name)])
    out.table([("NOMBRE", "name")], [{"name": name}])
    assert capsys.readouterr().out.count(name) == 2


def test_empty_no_color_does_not_disable_color(tty, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "")
    assert output.Out(as_json=False).styled is True
