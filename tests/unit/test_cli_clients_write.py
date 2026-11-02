"""`clients add/edit/enable/disable/rotate-key/remove` (M14a, #293)."""

import json

import pytest
from click.testing import CliRunner

from src.cli import clients
from src.cli.app import cli
from tests.fakes.cli_api import FakeApi

KEY = "k" * 43
A = {"id": "aaaa1111-0000", "name": "Jota-Screen", "is_active": True, "key_fp": "1a2b3c4d"}
LIST = {("GET", "/admin/clients"): [A]}
PATH = f"/admin/clients/{A['id']}"


def run(*args, input=None):
    return CliRunner().invoke(cli, list(args), input=input)


@pytest.fixture()
def tty(monkeypatch):
    monkeypatch.setattr(clients, "_stdin_isatty", lambda: True)


# --- add ---


def test_add_shows_key_once_with_warning(use_api):
    fake = use_api(FakeApi({("POST", "/admin/clients"): {**A, "client_key": KEY}}))
    r = run("clients", "add", "--name", "Jota-Screen", "--type", "web", "--agent", "main",
            "--allowed-agent", "main", "--allowed-agent", "home")
    assert r.exit_code == 0, r.output
    assert r.output.count(KEY) == 1
    assert "no se volverá a mostrar" in r.output
    assert fake.calls == [("POST", "/admin/clients", {
        "name": "Jota-Screen", "client_type": "web", "default_agent": "main",
        "allowed_agents": ["main", "home"]})]


def test_add_minimal_sends_only_name(use_api):
    fake = use_api(FakeApi({("POST", "/admin/clients"): {**A, "client_key": KEY}}))
    assert run("clients", "add", "--name", "X").exit_code == 0
    assert fake.calls[0][2] == {"name": "X"}


def test_add_json_includes_key(use_api):
    use_api(FakeApi({("POST", "/admin/clients"): {**A, "client_key": KEY}}))
    r = run("--json", "clients", "add", "--name", "X")
    assert json.loads(r.output)["client_key"] == KEY


def test_add_import_key_reads_stdin_not_argv(use_api):
    imported = "i" * 40
    fake = use_api(FakeApi({("POST", "/admin/clients"): {**A, "client_key": imported}}))
    r = run("clients", "add", "--name", "X", "--import-key", input=imported + "\n")
    assert r.exit_code == 0, r.output
    assert fake.calls[0][2] == {"name": "X", "client_key": imported}


def test_add_import_key_empty_stdin_is_usage_error(use_api):
    use_api(FakeApi())
    assert run("clients", "add", "--name", "X", "--import-key", input="\n").exit_code == 2


# --- edit ---


def test_edit_sends_patch_with_set_values_parsed_as_json(use_api):
    fake = use_api(FakeApi({**LIST, ("PATCH", PATH): A}))
    r = run("clients", "edit", "Jota-Screen", "--name", "Tele", "--set", "tts_speed=1.2",
            "--set", "push_enabled=false", "--set", "output_mode=[\"text\"]",
            "--set", "tts_voice=af_heart", "--set", "default_agent=null")
    assert r.exit_code == 0, r.output
    assert fake.calls[-1] == ("PATCH", PATH, {
        "name": "Tele", "tts_speed": 1.2, "push_enabled": False, "output_mode": ["text"],
        "tts_voice": "af_heart", "default_agent": None})


def test_edit_without_changes_is_usage_error(use_api):
    use_api(FakeApi(LIST))
    assert run("clients", "edit", "Jota-Screen").exit_code == 2


def test_edit_bad_set_is_usage_error(use_api):
    use_api(FakeApi(LIST))
    assert run("clients", "edit", "Jota-Screen", "--set", "sin-igual").exit_code == 2


# --- enable / disable ---


@pytest.mark.parametrize(("cmd", "active"), [("enable", True), ("disable", False)])
def test_enable_disable(use_api, cmd, active):
    fake = use_api(FakeApi({**LIST, ("PATCH", PATH): {**A, "is_active": active}}))
    r = run("clients", cmd, "aaaa1111")
    assert r.exit_code == 0, r.output
    assert fake.calls[-1] == ("PATCH", PATH, {"is_active": active})


# --- rotate-key / remove: confirmación ---


def test_rotate_without_tty_and_without_yes_fails_without_calling(use_api):
    fake = use_api(FakeApi(LIST))
    r = run("clients", "rotate-key", "Jota-Screen")
    assert r.exit_code == 2
    assert "--yes" in r.output
    assert ("POST", f"{PATH}/rotate-key", None) not in fake.calls


def test_rotate_with_yes_shows_new_key_once(use_api):
    use_api(FakeApi({**LIST, ("POST", f"{PATH}/rotate-key"): {**A, "client_key": KEY}}))
    r = run("--yes", "clients", "rotate-key", "Jota-Screen")
    assert r.exit_code == 0, r.output
    assert r.output.count(KEY) == 1


def test_rotate_asks_on_tty_and_aborts_on_no(use_api, tty):
    fake = use_api(FakeApi(LIST))
    r = run("clients", "rotate-key", "Jota-Screen", input="n\n")
    assert r.exit_code == 1
    assert len(fake.calls) == 1  # solo el listado para resolver la referencia


def test_remove_asks_on_tty_and_deletes_on_yes(use_api, tty):
    fake = use_api(FakeApi({**LIST, ("DELETE", PATH): None}))
    r = run("clients", "remove", "Jota-Screen", input="y\n")
    assert r.exit_code == 0, r.output
    assert fake.calls[-1] == ("DELETE", PATH, None)


def test_remove_without_tty_needs_yes(use_api):
    fake = use_api(FakeApi(LIST))
    assert run("clients", "remove", "Jota-Screen").exit_code == 2
    assert len(fake.calls) == 1
