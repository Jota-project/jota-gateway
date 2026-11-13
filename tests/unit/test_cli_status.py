"""`fogar-gw status` y `fogar-gw version` (M14a, #293)."""

import json

from click.testing import CliRunner

from fogar_gateway._version import __version__
from fogar_gateway.cli import api
from fogar_gateway.cli.app import cli
from tests.fakes.cli_api import FakeApi

STATE = {
    "gateway": {"started_at": "2026-11-02T10:00:00", "uptime_s": 3725.4, "version": "1.23.0"},
    "services": {
        "orchestrator": {"state": "CONNECTED", "last_error": None},
        "stt": {"provider": "elevenlabs", "state": "CONNECTED", "last_error": None},
        "tts": {"provider": "fogar", "state": "DISCONNECTED", "last_error": "ConnectError"},
    },
    "totals": {"sessions_active": 2, "sessions_opened": 9},
    "sessions": [],
}


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
    monkeypatch.delenv("FOGAR_GW_TOKEN", raising=False)
    r = run("version")
    assert r.exit_code == 0
    assert __version__ in r.output


# --- robustez (#306) ---


def test_status_tolerates_a_partial_state(use_api):
    use_api(FakeApi({("GET", "/admin/state"): {"gateway": {}, "services": {"stt": {}}}}))
    r = run("status")
    assert r.exit_code == 0, r.output
    assert "?" in r.output


def test_version_distinguishes_no_version_from_no_answer(use_api):
    use_api(FakeApi({("GET", "/admin/state"): {"gateway": {"uptime_s": 1}}}))
    r = run("version")
    assert r.exit_code == 0
    assert "sin versión" in r.output and "no responde" not in r.output


# --- acceso a OpenClaw (M19) ---


def _with_access(**orch):
    base = {"state": "CONNECTED", "last_error": None, "scopes": None, "admin": False,
            "auth_method": None, "pairing": "unknown", "pairing_request_id": None}
    return {**STATE, "services": {**STATE["services"], "orchestrator": {**base, **orch}}}


def test_status_shows_openclaw_access(use_api):
    state = _with_access(scopes=["operator.read", "operator.write", "operator.admin"], admin=True,
                         auth_method="device_token", pairing="paired")
    use_api(FakeApi({("GET", "/admin/state"): state}))
    r = run("status")
    assert r.exit_code == 0, r.output
    assert "admin · token de dispositivo · emparejado" in r.output


def test_status_shows_pending_pairing_with_the_command(use_api):
    state = _with_access(state="RECONNECTING", last_error="orchestrator_pairing_required",
                         pairing="pending", pairing_request_id="req-3")
    use_api(FakeApi({("GET", "/admin/state"): state}))
    r = run("status")
    assert "openclaw devices approve req-3" in r.output


def test_status_without_access_fields_has_no_access_line(use_api):
    use_api(FakeApi({("GET", "/admin/state"): STATE}))
    assert "acceso OpenClaw" not in run("status").output
