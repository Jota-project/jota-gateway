"""`jota-gw pair` (#317): abre un código y espera a que un cliente lo use."""

import json

import pytest
from click.testing import CliRunner

from jota_gateway.cli import pair as pair_mod
from jota_gateway.cli.app import cli
from tests.fakes.cli_api import FakeApi

ITEM = {"id": "p1", "code": "482913", "expires_at": "2026-11-08T17:10:00+00:00",
        "state": "open", "template": {}}
POLL = ("GET", "/admin/pairing/p1")


class PollingApi(FakeApi):
    """Como FakeApi, pero el sondeo devuelve los estados en orden (o lanza lo que se le dé)."""

    def __init__(self, states, responses=None):
        super().__init__({("POST", "/admin/pairing"): ITEM,
                          ("GET", "/.well-known/jota-gateway"): {"name": "green-house"},
                          ("DELETE", "/admin/pairing/p1"): None, **(responses or {})})
        self.states = list(states)

    def request(self, method, path, body=None, accept=()):
        if (method, path) == POLL:
            self.calls.append((method, path, body))
            state = self.states.pop(0)
            if isinstance(state, BaseException):
                raise state
            return {**ITEM, **state}
        return super().request(method, path, body)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(pair_mod, "_sleep", lambda s: None)


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_pair_shows_code_and_waits_until_used(use_api):
    fake = use_api(PollingApi([{}, {}, {"state": "used", "client_id": "c0ffee00-1",
                                      "client_name": "tele"}]))
    r = run("pair", "--type", "screen")
    assert r.exit_code == 0, r.output
    assert "482 913" in r.output and "green-house" in r.output
    assert "Cliente «tele» emparejado (id c0ffee00)" in r.output
    assert [c[:2] for c in fake.calls].count(POLL) == 3


@pytest.mark.parametrize("state,word", [("expired", "caducado"), ("cancelled", "anulado")])
def test_pair_closed_without_use_exits_1(use_api, state, word):
    use_api(PollingApi([{"state": state}]))
    r = run("pair")
    assert r.exit_code == 1
    assert word in r.output


def test_pair_ctrl_c_cancels_code(use_api):
    fake = use_api(PollingApi([{}, KeyboardInterrupt()]))
    r = run("pair")
    assert r.exit_code == 1
    assert "Código anulado" in r.output
    assert ("DELETE", "/admin/pairing/p1", None) in fake.calls


def test_pair_error_while_waiting_cancels_code(use_api):
    from jota_gateway.cli.api import ApiError

    fake = use_api(PollingApi([{}, ApiError("El gateway no responde", 3)]))
    r = run("pair")
    assert r.exit_code == 3
    assert ("DELETE", "/admin/pairing/p1", None) in fake.calls


def test_pair_no_wait_json(use_api):
    fake = use_api(PollingApi([]))
    r = run("--json", "pair", "--no-wait")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == {"id": "p1", "code": "482913", "expires_at": ITEM["expires_at"]}
    assert [c[0] for c in fake.calls] == ["POST"]


def test_pair_no_wait_text(use_api):
    use_api(PollingApi([]))
    r = run("pair", "--no-wait")
    assert r.exit_code == 0
    assert "482 913" in r.output and "Caduca" in r.output


def test_pair_set_rejects_unknown_field(use_api):
    fake = use_api(PollingApi([]))
    r = run("pair", "--set", "tts_vocie=ef_dora")
    assert r.exit_code == 2
    assert "tts_vocie" in r.output
    assert fake.calls == []


def test_pair_template_body(use_api):
    fake = use_api(PollingApi([]))
    r = run("pair", "--no-wait", "--name", "tele", "--type", "screen", "--agent", "j-assistant",
            "--allowed-agent", "j-assistant", "--set", "tts_voice=ef_dora",
            "--set", "barge_in_enabled:=false")
    assert r.exit_code == 0, r.output
    assert fake.calls[0] == ("POST", "/admin/pairing", {
        "name": "tele", "client_type": "screen", "default_agent": "j-assistant",
        "allowed_agents": ["j-assistant"], "tts_voice": "ef_dora", "barge_in_enabled": False})
