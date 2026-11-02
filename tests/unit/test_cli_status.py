"""`jota-gw status` y `jota-gw version` (M14a, #293)."""

import json

import pytest
from click.testing import CliRunner

from src._version import __version__
from src.cli import api
from src.cli.app import cli

STATE = {
    "gateway": {"started_at": "2026-11-02T10:00:00", "uptime_s": 3725.4, "version": "1.23.0"},
    "services": {
        "orchestrator": {"state": "CONNECTED", "last_error": None},
        "stt": {"provider": "elevenlabs", "state": "CONNECTED", "last_error": None},
        "tts": {"provider": "jota", "state": "DISCONNECTED", "last_error": "ConnectError"},
    },
    "totals": {"sessions_active": 2, "sessions_opened": 9},
    "sessions": [],
}


class FakeApi:
    def __init__(self, responses=None, error=None):
        self.responses, self.error, self.calls = responses or {}, error, []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.error:
            raise self.error
        return self.responses[(method, path)]


@pytest.fixture()
def use_api(monkeypatch):
    def _use(fake):
        monkeypatch.setattr(api, "from_context", lambda ctx: fake)
        return fake

    return _use


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_status_shows_gateway_and_services(use_api):
    use_api(FakeApi({("GET", "/admin/state"): STATE}))
    r = run("status")
    assert r.exit_code == 0, r.output
    for text in ("1.23.0", "1 h 2 min", "CONNECTED", "elevenlabs", "DISCONNECTED", "ConnectError"):
        assert text in r.output
    assert "2" in r.output


def test_status_json_is_the_state(use_api):
    use_api(FakeApi({("GET", "/admin/state"): STATE}))
    r = run("--json", "status")
    assert r.exit_code == 0
    assert json.loads(r.output) == STATE


def test_status_unreachable_exits_3(use_api):
    use_api(FakeApi(error=api.ApiError("El gateway no responde en 127.0.0.1:8004.", 3)))
    r = run("status")
    assert r.exit_code == 3
    assert "no responde" in r.output


def test_version_shows_cli_and_gateway(use_api):
    use_api(FakeApi({("GET", "/admin/state"): STATE}))
    r = run("version")
    assert r.exit_code == 0
    assert __version__ in r.output and "1.23.0" in r.output


def test_version_without_gateway_still_exits_0(use_api):
    use_api(FakeApi(error=api.ApiError("no responde", 3)))
    r = run("--json", "version")
    assert r.exit_code == 0
    assert json.loads(r.output) == {"cli": __version__, "gateway": None}


def test_version_without_token_still_exits_0(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("JOTA_GW_TOKEN", raising=False)
    r = run("version")
    assert r.exit_code == 0
    assert __version__ in r.output
