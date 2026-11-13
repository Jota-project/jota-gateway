"""Menú interactivo de `fogar-gw` sin argumentos (M14b, #294)."""

import pytest
import questionary
from click.testing import CliRunner

from fogar_gateway.cli import app, menu
from fogar_gateway.cli.api import ApiError
from tests.fakes.cli_api import FakeApi

A = {"id": "aaaa1111-0000", "name": "Fogar TV", "is_active": True, "key_fp": "1a2b3c4d",
     "tts_speed": 1.0, "created_at": "2026-11-02"}
STATE = {"gateway": {"version": "1.23.0", "uptime_s": 10}, "services": {}, "totals": {}}


@pytest.fixture()
def answers(monkeypatch):
    """Respuestas en orden a cada pregunta de questionary; None = Ctrl+C."""
    queue: list = []

    class _Q:
        def __init__(self, *args, **kwargs):
            pass

        def ask(self):
            return queue.pop(0) if queue else None

    for name in ("select", "text"):
        monkeypatch.setattr(questionary, name, _Q)
    monkeypatch.setattr(menu, "interactive", lambda: True)
    return queue


def run(*args):
    return CliRunner().invoke(app.cli, list(args))


def test_no_args_on_a_terminal_opens_the_menu(answers):
    answers.append("exit")
    r = run()
    assert r.exit_code == 0
    assert "Usage:" not in r.output


@pytest.mark.parametrize(("args", "tty"), [((), False), (("--json",), True)])
def test_menu_needs_a_terminal_and_no_json(monkeypatch, args, tty):
    monkeypatch.setattr(menu, "interactive", lambda: tty)
    monkeypatch.setattr(menu, "run_menu", lambda ctx: pytest.fail("no debía abrirse"))
    r = run(*args)
    assert r.exit_code == 0
    assert "Usage:" in r.output


def test_ctrl_c_leaves_cleanly(answers):
    r = run()
    assert r.exit_code == 0


def test_status_from_the_menu(answers, use_api):
    use_api(FakeApi({("GET", "/admin/state"): STATE}))
    answers.extend(["status", "exit"])
    r = run()
    assert r.exit_code == 0, r.output
    assert "1.23.0" in r.output


def test_disable_a_client_picked_from_a_list(answers, use_api):
    fake = use_api(FakeApi({("GET", "/admin/clients"): [A],
                            ("PATCH", f"/admin/clients/{A['id']}"): {**A, "is_active": False}}))
    answers.extend(["clients", "disable", A["id"], "exit"])
    r = run()
    assert r.exit_code == 0, r.output
    assert ("PATCH", f"/admin/clients/{A['id']}", {"is_active": False}) in fake.calls


def test_edit_a_field_from_the_menu(answers, use_api):
    fake = use_api(FakeApi({("GET", "/admin/clients"): [A],
                            ("PATCH", f"/admin/clients/{A['id']}"): A}))
    answers.extend(["clients", "edit", A["id"], "tts_speed", "1.3", "exit"])
    assert run().exit_code == 0
    assert ("PATCH", f"/admin/clients/{A['id']}", {"tts_speed": "1.3"}) in fake.calls


def test_create_a_client_from_the_menu(answers, use_api):
    fake = use_api(FakeApi({("POST", "/admin/clients"): {**A, "client_key": "k" * 43}}))
    answers.extend(["clients", "add", "Tele", "web", "exit"])
    r = run()
    assert r.exit_code == 0, r.output
    assert fake.calls[0] == ("POST", "/admin/clients", {"name": "Tele", "client_type": "web"})
    assert "k" * 43 in r.output


def test_an_error_is_shown_and_the_menu_goes_on(answers, use_api):
    use_api(FakeApi(error=ApiError("El gateway no responde en 127.0.0.1:8004.", 3)))
    answers.extend(["status", "exit"])
    r = run()
    assert r.exit_code == 0
    assert "no responde" in r.output


def test_menu_module_import_is_light():
    import subprocess
    import sys
    from pathlib import Path

    code = ("import sys, fogar_gateway.cli.menu, fogar_gateway.cli.app; "
            "print('HEAVY=' + ','.join(m for m in ('questionary','prompt_toolkit','rich') "
            "if m in sys.modules))")
    r = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2],
                       capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "HEAVY="


def test_menu_client_types_match_client_type():
    # El menú repite la lista para no importar pydantic al arrancar; no puede quedarse atrás.
    from typing import get_args

    from fogar_gateway.models.admin_schemas import ClientType

    assert list(get_args(ClientType)) == menu.CLIENT_TYPES
