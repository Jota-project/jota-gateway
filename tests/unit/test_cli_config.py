"""`jota-gw config show | get | validate` (M14b, #294)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from jota_gateway.cli.app import cli

ROOT = Path(__file__).resolve().parents[2]
SECRET = "s3cr3t-admin-token-0123456789"


@pytest.fixture()
def home(monkeypatch, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setenv("JOTA_GATEWAY_HOME", str(base))
    for name in ("ADMIN_TOKEN", "OPENCLAW_TOKEN", "TTS_WS_URL", "STT_PROVIDER", "LOG_FORMAT"):
        monkeypatch.delenv(name, raising=False)
    (base / "config.env").write_text(
        f"ADMIN_TOKEN={SECRET}\nOPENCLAW_TOKEN=otro-secreto-largo\nLOG_FORMAT=json\n"
    )
    return base


def run(*args, input=None):
    return CliRunner().invoke(cli, list(args), input=input)


def test_show_gives_origin_and_masks_secrets(home, monkeypatch):
    monkeypatch.setenv("TTS_WS_URL", "tts.local:8005")
    r = run("config", "show")
    assert r.exit_code == 0, r.output
    lines = {line.split()[0]: line for line in r.output.splitlines() if line.strip()}
    assert "json" in lines["LOG_FORMAT"] and "config.env" in lines["LOG_FORMAT"]
    assert "tts.local:8005" in lines["TTS_WS_URL"] and "entorno" in lines["TTS_WS_URL"]
    assert "por defecto" in lines["TURN_TIMEOUT_S"]
    assert "definido" in lines["ADMIN_TOKEN"]
    assert "vacío" in lines["TRANSCRIBER_TOKEN"]
    assert SECRET not in r.output and "otro-secreto-largo" not in r.output


def test_show_json_masks_secrets(home):
    r = run("--json", "config", "show")
    data = json.loads(r.output)
    assert data["LOG_FORMAT"] == {"value": "json", "origin": str(home / "config.env")}
    assert data["ADMIN_TOKEN"]["value"] == "•••• (definido)"
    assert SECRET not in r.output


def test_get_is_case_insensitive(home):
    r = run("config", "get", "log_format")
    assert r.exit_code == 0
    assert "json" in r.output


def test_get_secret_is_masked(home):
    r = run("--json", "config", "get", "ADMIN_TOKEN")
    assert SECRET not in r.output
    assert json.loads(r.output)["value"] == "•••• (definido)"


def test_get_unknown_suggests_and_exits_2(home):
    r = run("config", "get", "ADMIN_TOKN")
    assert r.exit_code == 2
    assert "ADMIN_TOKEN" in r.output


def test_validate_ok(home):
    r = run("config", "validate")
    assert r.exit_code == 0, r.output


def test_validate_invalid_value_names_field_not_value(home):
    (home / "config.env").write_text(f"ADMIN_TOKEN={SECRET}\nSTT_PROVIDER=valor-raro-xyz\n")
    r = run("config", "validate")
    assert r.exit_code == 1
    assert "STT_PROVIDER" in r.output and "valor-raro-xyz" not in r.output


def test_validate_empty_admin_token_fails(home):
    (home / "config.env").write_text("OPENCLAW_TOKEN=otro-secreto-largo\n")
    r = run("config", "validate")
    assert r.exit_code == 1
    assert "ADMIN_TOKEN" in r.output


def test_missing_config_env_still_works_with_defaults(home):
    (home / "config.env").unlink()
    r = run("config", "get", "LOG_FORMAT")
    assert r.exit_code == 0
    assert "por defecto" in r.output


def test_cwd_dotenv_is_never_read_even_if_broken(tmp_path):
    # Settings solo lee <base>/config.env: un .env roto en el directorio actual no cuenta (M14c).
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".env").write_text("ADMIN_TOKEN=del-cwd\nSTT_PROVIDER=roto\n")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "JOTA_GATEWAY_HOME": str(tmp_path / "vacia")}
    env.pop("ADMIN_TOKEN", None)
    env.pop("STT_PROVIDER", None)
    r = subprocess.run(
        [sys.executable, "-m", "jota_gateway.cli", "--json", "config", "get", "ADMIN_TOKEN"],
        cwd=cwd, env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["value"] == "(vacío)"


def test_get_secret_plain_is_masked(home):
    r = run("config", "get", "ADMIN_TOKEN")
    assert r.exit_code == 0
    assert SECRET not in r.output and "definido" in r.output


def test_database_url_password_is_masked(home, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw-secreta-123@db.local/gw")
    for args in (("config", "show"), ("--json", "config", "get", "DATABASE_URL")):
        r = run(*args)
        assert "pw-secreta-123" not in r.output
        assert "user:" in r.output and "db.local" in r.output


# --- config set / unset (M14d, #296) ---------------------------------------------------------

SPECIAL = ["a b", "x#y", '"q"', "k=v", "$HOME", "it's \\ ok", ""]


def _env(home):
    return home / "config.env"


@pytest.mark.parametrize("value", SPECIAL)
def test_set_roundtrips_special_values(home, value):
    from dotenv import dotenv_values

    from jota_gateway.cli import config as cfg

    r = run("config", "set", "TTS_WS_URL", value)
    assert r.exit_code == 0, r.output
    assert dotenv_values(_env(home))["TTS_WS_URL"] == value
    settings, errors, _ = cfg.load()
    assert errors == [] and value == settings.TTS_WS_URL


def test_set_value_with_interpolation_is_refused(home):
    before = _env(home).read_bytes()
    r = run("config", "set", "TTS_WS_URL", "${HOME}x")
    assert r.exit_code == 1
    assert _env(home).read_bytes() == before


def test_set_invalid_writes_nothing(home):
    before = _env(home).read_bytes()
    r = run("config", "set", "STT_PROVIDER", "nope")
    assert r.exit_code == 1
    assert _env(home).read_bytes() == before
    assert [p.name for p in home.iterdir()] == ["config.env"]


def test_set_new_failing_check_aborts(home):
    # Vaciar ADMIN_TOKEN crea un check `fail` nuevo (el sin definir no lo era).
    before = _env(home).read_bytes()
    r = run("config", "set", "ADMIN_TOKEN", "-", input="\n")
    assert r.exit_code == 1
    assert _env(home).read_bytes() == before


def test_set_keeps_comments_and_mode_600(home):
    _env(home).write_text("# hola\nexport LOG_FORMAT=json\n\nADMIN_TOKEN=abc123\n# fin\n")
    _env(home).chmod(0o644)
    r = run("config", "set", "log_format", "text")
    assert r.exit_code == 0, r.output
    assert _env(home).read_text() == "# hola\nLOG_FORMAT=text\n\nADMIN_TOKEN=abc123\n# fin\n"
    assert _env(home).stat().st_mode & 0o777 == 0o600
    run("config", "set", "TURN_TIMEOUT_S", "30")
    assert _env(home).read_text().endswith("# fin\nTURN_TIMEOUT_S=30\n")


def test_set_secret_as_argument_is_usage_error(home):
    before = _env(home).read_bytes()
    r = run("config", "set", "ADMIN_TOKEN", "nuevo-secreto")
    assert r.exit_code == 2
    assert _env(home).read_bytes() == before


def test_set_secret_from_stdin(home):
    r = run("config", "set", "OPENCLAW_TOKEN", "-", input="nuevo-secreto-largo\n")
    assert r.exit_code == 0, r.output
    assert "OPENCLAW_TOKEN=nuevo-secreto-largo" in _env(home).read_text()
    assert "nuevo-secreto-largo" not in r.output


def test_set_secret_without_value_or_tty_is_usage_error(home):
    assert run("config", "set", "OPENCLAW_TOKEN").exit_code == 2


def test_unset_removes_line(home):
    r = run("config", "unset", "log_format")
    assert r.exit_code == 0, r.output
    assert "LOG_FORMAT" not in _env(home).read_text()
    assert run("config", "unset", "LOG_FORMAT").exit_code == 0  # ya no estaba


def test_set_unknown_name_suggests(home):
    r = run("config", "set", "LOG_FORMT", "json")
    assert r.exit_code == 2
    assert "¿Querías decir LOG_FORMAT?" in r.output
    assert run("config", "unset", "LOG_FORMT").exit_code == 2


def test_set_without_config_env_suggests_init(home):
    _env(home).unlink()
    r = run("config", "set", "LOG_FORMAT", "json")
    assert r.exit_code == 1 and "jota-gw init" in r.output
    assert not _env(home).exists()


def test_set_warns_when_environment_wins(home, monkeypatch):
    monkeypatch.setenv("TURN_TIMEOUT_S", "99")
    r = run("config", "set", "TURN_TIMEOUT_S", "30")
    assert r.exit_code == 0 and "entorno" in r.output


@pytest.fixture()
def systemd(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from jota_gateway.cli import output, service

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    unit = service.unit_path()
    unit.parent.mkdir(parents=True)
    unit.write_text("[Unit]\n")
    st = SimpleNamespace(calls=[], active=True, tty=False, healthy=True, fail=False)
    monkeypatch.setattr(service, "require_systemd", lambda: None)
    monkeypatch.setattr(service, "_probe", lambda url: st.healthy)
    monkeypatch.setattr(service.time, "sleep", lambda s: None)
    monkeypatch.setattr(service, "HEALTH_TIMEOUT_S", 0.05)
    monkeypatch.setattr(output, "_stdin_isatty", lambda: st.tty)

    def fake(*args, check=True):
        st.calls.append(args)
        rc = 0 if st.active or args[0] != "is-active" else 3
        if st.fail and args[0] == "restart":
            raise subprocess.CalledProcessError(1, args, "", "boom")
        return subprocess.CompletedProcess(args, rc, "", "")

    monkeypatch.setattr(service, "systemctl", fake)
    return st


def test_set_offers_restart_when_active(home, systemd):
    r = run("config", "set", "LOG_FORMAT", "text")
    assert r.exit_code == 0 and "jota-gw restart" in r.output
    assert ("restart", "jota-gateway.service") not in systemd.calls
    systemd.calls.clear()
    r = run("--yes", "config", "set", "LOG_FORMAT", "json")
    assert ("restart", "jota-gateway.service") in systemd.calls
    systemd.calls.clear()
    systemd.tty = True
    r = run("config", "set", "LOG_FORMAT", "text", input="n\n")  # decir que no no aborta
    assert r.exit_code == 0 and ("restart", "jota-gateway.service") not in systemd.calls
    r = run("config", "set", "LOG_FORMAT", "json", input="y\n")
    assert ("restart", "jota-gateway.service") in systemd.calls


def test_set_no_restart_when_inactive_or_no_unit(home, systemd):
    systemd.active = False
    run("--yes", "config", "set", "LOG_FORMAT", "text")
    assert ("restart", "jota-gateway.service") not in systemd.calls


# --- ronda de arreglo 1 ----------------------------------------------------------------------


def test_set_validates_without_environment_of_name(home, monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "jota")
    before = _env(home).read_bytes()
    r = run("config", "set", "STT_PROVIDER", "nope")
    assert r.exit_code == 1
    assert _env(home).read_bytes() == before
    assert os.environ["STT_PROVIDER"] == "jota"  # restaurada


@pytest.mark.parametrize("value", ["a\nb", "a\rb"])
def test_set_rejects_newlines(home, value):
    before = _env(home).read_bytes()
    assert run("config", "set", "TTS_WS_URL", value).exit_code == 2
    assert _env(home).read_bytes() == before


def test_set_rejects_newlines_from_stdin(home):
    before = _env(home).read_bytes()
    r = run("config", "set", "OPENCLAW_TOKEN", "-", input="uno\ndos\n")
    assert r.exit_code == 2
    assert _env(home).read_bytes() == before


def test_set_json_yes_is_valid_json(home, systemd):
    r = run("--json", "--yes", "config", "set", "LOG_FORMAT", "text")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == {"name": "LOG_FORMAT", "value": "text", "changed": True,
                                    "restarted": True}
    assert ("restart", "jota-gateway.service") in systemd.calls


def test_set_report_masks_secret_and_ignores_environment(home, monkeypatch):
    monkeypatch.setenv("OPENCLAW_TOKEN", "del-entorno-xyz")
    r = run("--json", "config", "set", "OPENCLAW_TOKEN", "-", input="guardado-abc\n")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["value"] == "•••• (definido)"
    assert "guardado-abc" not in r.output and "del-entorno-xyz" not in r.output
    monkeypatch.setenv("TURN_TIMEOUT_S", "99")
    r = run("--json", "config", "set", "TURN_TIMEOUT_S", "30")
    assert json.loads(r.output)["value"] == "30"


# --- tanda final B ----------------------------------------------------------------------------


def test_set_database_url_password_never_shown(home):
    url = "postgresql://u:pw-secreta-123@h/db"
    for args in (("config", "set", "DATABASE_URL", url), ("--json", "config", "set",
                                                           "DATABASE_URL", url + "x")):
        r = run(*args)
        assert r.exit_code == 0, r.output
        assert "pw-secreta-123" not in r.output
        assert "u:••••@h" in r.output


def test_set_restart_failure_exits_1_with_hint(home, systemd):
    systemd.fail = True
    r = run("--yes", "config", "set", "LOG_FORMAT", "text")
    assert r.exit_code == 1 and not isinstance(r.exception, subprocess.CalledProcessError)
    assert "LOG_FORMAT=text" in _env(home).read_text()  # guardado
    assert "guardado" in r.output and "jota-gw logs -n 50" in r.output


def test_set_restart_not_healthy_exits_1(home, systemd):
    systemd.healthy = False
    r = run("--yes", "config", "set", "LOG_FORMAT", "text")
    assert r.exit_code == 1 and "jota-gw logs -n 50" in r.output
    assert "guardado" in r.output


def test_set_restart_failure_json_keeps_stdout_clean(home, systemd):
    systemd.fail = True
    r = CliRunner().invoke(cli, ["--json", "--yes", "config", "set",
                                                 "LOG_FORMAT", "text"])
    assert r.exit_code == 1 and r.stdout == ""
    assert "guardado" in r.stderr


def test_set_restart_waits_for_healthz(home, systemd, monkeypatch):
    answers = iter([False, False, True])
    monkeypatch.setattr(service_mod(), "_probe", lambda url: next(answers))
    r = run("--yes", "config", "set", "LOG_FORMAT", "text")
    assert r.exit_code == 0, r.output
    assert list(answers) == []


def service_mod():
    from jota_gateway.cli import service

    return service


def test_set_secret_dash_with_tty_prompts_hidden(home, systemd, monkeypatch):
    import click

    seen = {}
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: seen.update(kw) or "tecleado-oculto")
    systemd.tty = True
    systemd.active = False
    r = run("config", "set", "OPENCLAW_TOKEN", "-")
    assert r.exit_code == 0, r.output
    assert seen["hide_input"] is True
    assert "OPENCLAW_TOKEN=tecleado-oculto" in _env(home).read_text()
    assert "tecleado-oculto" not in r.output  # click no hace eco con hide_input
