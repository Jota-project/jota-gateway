"""Raíz de la CLI jota-gw (M14a, #293)."""

import subprocess
import sys
import tomllib
from pathlib import Path

from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[2]


def test_entry_point_is_declared():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["scripts"]["jota-gw"] == "jota_gateway.cli.__main__:main"
    deps = " ".join(project["dependencies"])
    assert "click" in deps and "rich" in deps


def test_no_args_shows_help_and_exits_0():
    from jota_gateway.cli.app import cli

    r = CliRunner().invoke(cli, [])
    assert r.exit_code == 0
    assert "Usage:" in r.output


def test_python_m_jota_gateway_cli_help_runs():
    r = subprocess.run(
        [sys.executable, "-m", "jota_gateway.cli", "--help"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    assert "jota-gw" in r.stdout
    for opt in ("--url", "--token", "--json", "--yes"):
        assert opt in r.stdout


def test_lazy_group_lists_commands_without_importing_them(monkeypatch):
    from jota_gateway.cli import app

    monkeypatch.setitem(app.COMMANDS, "fake", ("jota_gateway.cli.no_existe:cmd", "Comando de prueba"))
    r = CliRunner().invoke(app.cli, ["--help"])
    assert r.exit_code == 0
    assert "fake" in r.output and "Comando de prueba" in r.output
