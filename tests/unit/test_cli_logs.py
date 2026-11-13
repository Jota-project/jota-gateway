"""`fogar-gw logs`: filtrado de líneas del journal y llamada a journalctl (M14d, #296)."""

import io
import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from fogar_gateway.cli import logs, service
from fogar_gateway.cli.app import cli

TEXT = "2026-11-03 10:00:00,123 | fogar_gateway.core | {} | sesión abierta sid-1"


def _json(level, **extra):
    return json.dumps({"ts": "2026-11-03T10:00:00+00:00", "level": level, "logger": "x",
                       "msg": "hola", **extra})


def test_matches_json_level_threshold():
    assert logs.matches(_json("WARNING"), "WARNING", None)
    assert logs.matches(_json("ERROR"), "WARNING", None)
    assert not logs.matches(_json("INFO"), "WARNING", None)


def test_matches_text_level():
    assert logs.matches(TEXT.format("ERROR"), "WARNING", None)
    assert not logs.matches(TEXT.format("INFO"), "WARNING", None)


def test_matches_session_json_field():
    assert logs.matches(_json("INFO", session_id="s1"), None, "s1")
    assert logs.matches(_json("INFO", client_id="s1"), None, "s1")
    assert not logs.matches(_json("INFO", session_id="s2"), None, "s1")
    assert not logs.matches(_json("INFO"), None, "s1")


def test_matches_session_text_substring():
    assert logs.matches(TEXT.format("INFO"), None, "sid-1")
    assert not logs.matches(TEXT.format("INFO"), None, "sid-2")


def test_unparseable_line_passes():
    assert logs.matches("Traceback (most recent call last):", "ERROR", None)
    assert logs.matches("{roto", "ERROR", None)
    assert logs.matches(json.dumps({"msg": "sin nivel"}), "ERROR", None)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    unit = tmp_path / "u.service"
    unit.write_text("")
    monkeypatch.setattr(service, "unit_path", lambda: unit)
    monkeypatch.setattr(service, "DOCKERENV", tmp_path / "no-dockerenv")
    monkeypatch.setattr(service, "require_systemd", lambda: None)
    monkeypatch.setattr(logs.shutil, "which", lambda name: "/usr/bin/journalctl")
    return unit


def test_no_filters_execs_journalctl(env, monkeypatch):
    got = []
    monkeypatch.setattr("os.execvp", lambda f, a: got.append((f, a)))
    r = CliRunner().invoke(cli, ["logs", "-f", "-n", "5"])
    assert r.exit_code == 0, r.output
    assert got == [("journalctl", ["journalctl", "--user", "-u", "fogar-gateway.service",
                                   "-o", "cat", "-n", "5", "-f"])]
    got.clear()
    CliRunner().invoke(cli, ["logs"])
    assert got[0][1][-2:] == ["-n", "100"]


def test_filters_use_popen_and_filter_lines(env, monkeypatch, capsys):
    import subprocess

    lines = [TEXT.format("INFO"), TEXT.format("ERROR")]
    seen = []

    def fake_popen(args, **kw):
        seen.append(args)
        return SimpleNamespace(stdout=io.StringIO("\n".join(lines) + "\n"),
                               terminate=lambda: None, wait=lambda: 0)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    r = CliRunner().invoke(cli, ["logs", "--level", "warning"])
    assert r.exit_code == 0, r.output
    assert seen[0][:7] == ["journalctl", "--user", "-u", "fogar-gateway.service", "-o", "cat", "-n"]
    assert "ERROR" in r.output and "INFO" not in r.output


def test_ctrl_c_terminates_popen_quietly(env, monkeypatch):
    import subprocess

    done = []

    class Boom:
        def __iter__(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "Popen", lambda a, **kw: SimpleNamespace(
        stdout=Boom(), terminate=lambda: done.append(1), wait=lambda: 0))
    r = CliRunner().invoke(cli, ["logs", "--session", "x"])
    assert r.exit_code == 0 and done == [1]


def test_no_unit_or_no_journalctl_gives_no_service_msg(env, monkeypatch):
    monkeypatch.setattr(logs.shutil, "which", lambda name: None)
    r = CliRunner().invoke(cli, ["logs"])
    assert r.exit_code == 1 and "No hay servicio instalado" in r.output
    env.unlink()
    r = CliRunner().invoke(cli, ["logs"])
    assert r.exit_code == 1 and "No hay servicio instalado" in r.output


def test_invalid_utf8_line_passes_and_loop_continues(env, monkeypatch):
    import subprocess
    import sys

    real = subprocess.Popen
    script = ("import sys; sys.stdout.buffer.write("
              "b'x | y | INFO | roto \\xff\\xfe\\n' b'x | y | ERROR | despues\\n')")
    # Proceso real con los kwargs que pasa `logs`: así se prueba la decodificación de verdad.
    monkeypatch.setattr(subprocess, "Popen",
                        lambda args, **kw: real([sys.executable, "-c", script], **kw))
    r = CliRunner().invoke(cli, ["logs", "--session", "|"])
    assert r.exit_code == 0, r.output
    assert "roto ��" in r.output and "despues" in r.output


def test_filtered_journalctl_failure_propagates_exit_code(env, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "Popen", lambda a, **kw: SimpleNamespace(
        stdout=io.StringIO(""), terminate=lambda: None, wait=lambda: 1, returncode=1))
    r = CliRunner().invoke(cli, ["logs", "--level", "error"])
    assert r.exit_code == 1 and "journalctl falló (código 1)" in r.output
