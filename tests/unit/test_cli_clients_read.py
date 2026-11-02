"""Clientes por referencia y `clients list/show` (M14a, #293)."""

import json

import pytest
from click.testing import CliRunner

from src.cli import refs
from src.cli.api import ApiError
from src.cli.app import cli
from tests.fakes.cli_api import FakeApi

A = {"id": "aaaa1111-0000", "name": "Jota-Screen", "client_type": "web", "is_active": True,
     "default_agent": "main", "allowed_agents": None, "key_fp": "1a2b3c4d"}
B = {"id": "aaaa2222-0000", "name": "HA", "client_type": "ha", "is_active": False,
     "default_agent": None, "allowed_agents": ["home"], "key_fp": "5e6f7a8b"}
C = {"id": "bbbb3333-0000", "name": "ha", "client_type": None, "is_active": True,
     "default_agent": None, "allowed_agents": None, "key_fp": "9c0d1e2f"}
CLIENTS = [A, B, C]


# --- refs ---


def test_ref_exact_id():
    assert refs.resolve(CLIENTS, "aaaa2222-0000") is B


def test_ref_unique_id_prefix():
    assert refs.resolve(CLIENTS, "bbbb") is C


def test_ref_prefix_shorter_than_4_is_not_an_id():
    with pytest.raises(ApiError) as e:
        refs.resolve(CLIENTS, "bbb")
    assert e.value.exit_code == 1


def test_ref_ambiguous_prefix_lists_ids():
    with pytest.raises(ApiError) as e:
        refs.resolve(CLIENTS, "aaaa")
    assert e.value.exit_code == 2
    assert "aaaa1111-0000" in e.value.message and "aaaa2222-0000" in e.value.message


def test_ref_name_case_insensitive():
    assert refs.resolve(CLIENTS, "jota-screen") is A


def test_ref_repeated_name_is_ambiguous():
    with pytest.raises(ApiError) as e:
        refs.resolve(CLIENTS, "Ha")
    assert e.value.exit_code == 2
    assert "aaaa2222-0000" in e.value.message and "bbbb3333-0000" in e.value.message


def test_ref_id_wins_over_name():
    named_like_id = {**C, "name": "aaaa1111-0000"}
    assert refs.resolve([A, named_like_id], "aaaa1111-0000") is A


def test_ref_not_found():
    with pytest.raises(ApiError) as e:
        refs.resolve(CLIENTS, "nadie")
    assert e.value.exit_code == 1
    assert "nadie" in e.value.message


# --- comandos ---


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_list_shows_name_short_id_and_fp(use_api):
    use_api(FakeApi({("GET", "/admin/clients"): CLIENTS}))
    r = run("clients", "list")
    assert r.exit_code == 0, r.output
    for text in ("Jota-Screen", "aaaa1111", "1a2b3c4d", "main", "sí", "no"):
        assert text in r.output
    assert "aaaa1111-0000" not in r.output


def test_list_empty(use_api):
    use_api(FakeApi({("GET", "/admin/clients"): []}))
    r = run("clients", "list")
    assert r.exit_code == 0
    assert "sin clientes" in r.output.lower()


def test_list_json(use_api):
    use_api(FakeApi({("GET", "/admin/clients"): CLIENTS}))
    r = run("--json", "clients", "list")
    assert json.loads(r.output) == CLIENTS


def test_show_by_name(use_api):
    fake = use_api(
        FakeApi({("GET", "/admin/clients"): CLIENTS, ("GET", f"/admin/clients/{B['id']}"): B})
    )
    r = run("clients", "show", "aaaa2222")
    assert r.exit_code == 0, r.output
    assert "home" in r.output and "5e6f7a8b" in r.output
    assert fake.calls[-1] == ("GET", f"/admin/clients/{B['id']}", None)


def test_show_json(use_api):
    use_api(FakeApi({("GET", "/admin/clients"): CLIENTS, ("GET", f"/admin/clients/{A['id']}"): A}))
    r = run("--json", "clients", "show", "Jota-Screen")
    assert json.loads(r.output) == A


def test_show_ambiguous_exits_2(use_api):
    use_api(FakeApi({("GET", "/admin/clients"): CLIENTS}))
    assert run("clients", "show", "ha").exit_code == 2
