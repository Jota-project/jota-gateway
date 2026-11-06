"""`jota-gw onboard`: asistente de instalación (instalador mínimo, Task 5).

Nunca toca la red, systemd ni el ~/.config real: `verify.*`, `service.*`, `init`,
`doctor.run_checks` y la API de /admin van parcheados; la base y XDG_CONFIG_HOME son temporales.
"""

import json
import logging
import os
from types import SimpleNamespace

import pytest
import questionary
from click.testing import CliRunner
from dotenv import dotenv_values

from jota_gateway.cli import app, doctor, menu, output, service, verify
from jota_gateway.cli import init as init_mod
from jota_gateway.cli.verify import Verdict
from tests.fakes.cli_api import FakeApi

ADMIN = "admin-token-largo-0123456789"
OC_TOKEN = "oc-token-secreto-abcdef"
STT_TOKEN = "stt-token-secreto-456"
TTS_TOKEN = "tts-token-secreto-123"
EL_KEY = "el-clave-secreta-xyz"
CLIENT_KEY = "k" * 43
CLIENT = {"id": "aaaa1111-0000", "name": "smoke", "key_fp": "1a2b3c4d"}
SECRETS = (OC_TOKEN, STT_TOKEN, TTS_TOKEN, EL_KEY)
NO_OPENCLAW = "Sin OpenClaw no se instala el servicio. Vuelve a ejecutar `jota-gw onboard`."
ENV = ("ADMIN_TOKEN", "OPENCLAW_HOST", "OPENCLAW_PORT", "OPENCLAW_TOKEN", "STT_PROVIDER",
       "TTS_PROVIDER", "TRANSCRIBER_WS_URL", "TRANSCRIBER_TOKEN", "TTS_WS_URL", "TTS_TOKEN",
       "ELEVENLABS_API_KEY", "JOTA_GW_TOKEN", "JOTA_GW_URL")
REAL_INIT = init_mod.init


@pytest.fixture()
def gw(monkeypatch, tmp_path, use_api):
    """Base vacía (sin config.env), unidad en un XDG temporal y todo lo externo parcheado."""
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setenv("JOTA_GATEWAY_HOME", str(base))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    st = SimpleNamespace(calls=[], verdicts={}, detected=None, env=base / "config.env")

    def fake_init():
        st.calls.append("init")
        st.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")

    def fake_install():
        st.calls.append("install")
        service.unit_path().parent.mkdir(parents=True, exist_ok=True)
        service.unit_path().write_text("[Unit]\n")

    def fake_verb(ctx, verb, state, wait):
        st.calls.append(verb)

    def verifier(name):
        async def fake(*args):
            st.calls.append((name, *args))
            verdict = st.verdicts.get(name, Verdict(True, "ok", f"{name} ok."))
            if isinstance(verdict, list):
                verdict = verdict.pop(0)
            if isinstance(verdict, BaseException):
                raise verdict
            return verdict
        return fake

    async def detect(port=18789):
        return st.detected

    monkeypatch.setattr(init_mod, "init", fake_init)
    monkeypatch.setattr(service, "install", fake_install)
    monkeypatch.setattr(service, "_verb", fake_verb)
    monkeypatch.setattr(doctor, "run_checks", lambda url, token: [
        {"level": "fail", "check": "orquestador", "detail": "aviso del doctor"}])
    for name in ("verify_openclaw", "verify_jota_stt", "verify_jota_tts", "verify_elevenlabs"):
        monkeypatch.setattr(verify, name, verifier(name))
    monkeypatch.setattr(verify, "detect_openclaw", detect)
    st.api = use_api(FakeApi({("GET", "/admin/clients"): [],
                              ("POST", "/admin/clients"): {**CLIENT, "client_key": CLIENT_KEY}}))
    return st


@pytest.fixture()
def tty(monkeypatch):
    """Terminal interactiva: cada pregunta de questionary saca la siguiente respuesta de la
    cola (None = Ctrl+C) y queda anotada en `asked` como (tipo, mensaje)."""
    st = SimpleNamespace(queue=[], asked=[])

    def question(kind):
        class _Q:
            def __init__(self, message, *args, **kwargs):
                st.asked.append((kind, message))

            def ask(self):
                return st.queue.pop(0) if st.queue else None
        return _Q

    for kind in ("select", "text", "password", "confirm"):
        monkeypatch.setattr(questionary, kind, question(kind))
    monkeypatch.setattr(output, "_stdin_isatty", lambda: True)
    return st


def write_answers(tmp_path, mode=0o600, **over):
    data = {"openclaw_host": "127.0.0.1", "openclaw_port": 18789, "openclaw_token": OC_TOKEN,
            "stt": {"provider": "off"},
            "tts": {"provider": "jota", "url": "localhost:8005", "token": TTS_TOKEN},
            "first_client": {"name": "smoke", "type": "web"}, **over}
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(data))
    path.chmod(mode)
    return path


def run(*args):
    return CliRunner().invoke(app.cli, list(args))


def onboard(path):
    return run("onboard", "--answers", str(path))


def installs(gw):
    return [c for c in gw.calls if c in ("install", "restart")]


# --- los tests de la brief -----------------------------------------------------------------


def test_onboard_happy_path_answers_file(gw, tmp_path):
    path = write_answers(tmp_path, stt={"provider": "jota", "url": "localhost:9000"},
                         tts={"provider": "elevenlabs", "api_key": EL_KEY})
    r = onboard(path)
    assert r.exit_code == 0, r.output
    assert dotenv_values(gw.env) == {
        "ADMIN_TOKEN": ADMIN,
        "OPENCLAW_TOKEN": OC_TOKEN, "OPENCLAW_HOST": "127.0.0.1", "OPENCLAW_PORT": "18789",
        "TRANSCRIBER_WS_URL": "localhost:9000", "TRANSCRIBER_TOKEN": "", "STT_PROVIDER": "jota",
        "ELEVENLABS_API_KEY": EL_KEY, "TTS_PROVIDER": "elevenlabs",
    }
    assert gw.calls == [
        "init",
        ("verify_openclaw", "127.0.0.1", 18789, OC_TOKEN),
        ("verify_jota_stt", "localhost:9000", ""),
        ("verify_elevenlabs", "tts", EL_KEY),
        "install",
    ]
    assert ("POST", "/admin/clients", {"name": "smoke", "client_type": "web"}) in gw.api.calls
    assert r.output.count(CLIENT_KEY) == 1  # la clave, una sola vez
    assert "Recomendado: deja este servicio accesible solo desde el gateway." in r.output
    assert "aviso del doctor" in r.output  # el doctor solo informa: no cambia el código
    assert ":8004/ws/stream" in r.output and "ws://" in r.output


def test_onboard_openclaw_unverified_exits_without_install(gw, tmp_path):
    gw.verdicts["verify_openclaw"] = Verdict(False, "rejected", "OpenClaw rechazó el token.")
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 1
    assert "OpenClaw rechazó el token." in r.output
    assert installs(gw) == []
    assert dotenv_values(gw.env) == {"ADMIN_TOKEN": ADMIN}  # nada sin verificar
    assert not any(isinstance(c, tuple) and c[0] != "verify_openclaw" for c in gw.calls)


def test_onboard_voice_off_writes_off(gw, tmp_path):
    r = onboard(write_answers(tmp_path, stt={"provider": "off"}, tts={"provider": "off"}))
    assert r.exit_code == 0, r.output
    env = dotenv_values(gw.env)
    assert env["STT_PROVIDER"] == "off" and env["TTS_PROVIDER"] == "off"
    assert not {"TRANSCRIBER_WS_URL", "TTS_WS_URL", "TTS_TOKEN", "ELEVENLABS_API_KEY"} & set(env)
    assert [c[0] for c in gw.calls if isinstance(c, tuple)] == ["verify_openclaw"]


def test_onboard_rerun_restarts_not_installs(gw, tmp_path):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    service.unit_path().parent.mkdir(parents=True)
    service.unit_path().write_text("[Unit]\n")
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 0, r.output
    assert installs(gw) == ["restart"]
    assert "init" not in gw.calls


@pytest.mark.parametrize("why", ["clients_exist", "first_client_null"])
def test_onboard_skips_first_client_when_clients_exist(gw, tmp_path, why):
    if why == "clients_exist":
        gw.api.responses[("GET", "/admin/clients")] = [CLIENT]
        path = write_answers(tmp_path)
    else:
        path = write_answers(tmp_path, first_client=None)
    r = onboard(path)
    assert r.exit_code == 0, r.output
    assert [c for c in gw.api.calls if c[0] == "POST"] == []
    assert CLIENT_KEY not in r.output
    assert ":8004/ws/stream" in r.output  # la dirección sale igual


def test_onboard_interrupted_then_resumed(gw, tmp_path):
    path = write_answers(tmp_path, stt={"provider": "jota", "url": "localhost:9000",
                                        "token": STT_TOKEN})
    gw.verdicts["verify_jota_stt"] = KeyboardInterrupt()
    r = onboard(path)
    assert r.exit_code == 1
    env = dotenv_values(gw.env)
    assert env["OPENCLAW_TOKEN"] == OC_TOKEN and env["OPENCLAW_PORT"] == "18789"
    assert "STT_PROVIDER" not in env and "TRANSCRIBER_TOKEN" not in env
    assert not service.unit_path().exists() and installs(gw) == []

    del gw.verdicts["verify_jota_stt"]
    r = onboard(path)
    assert r.exit_code == 0, r.output
    env = dotenv_values(gw.env)
    assert env["STT_PROVIDER"] == "jota" and env["TRANSCRIBER_TOKEN"] == STT_TOKEN
    assert env["TTS_TOKEN"] == TTS_TOKEN
    assert gw.calls.count("init") == 1 and installs(gw) == ["install"]
    assert service.unit_path().exists()


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660])
def test_answers_file_must_be_0600(gw, tmp_path, mode):
    r = onboard(write_answers(tmp_path, mode=mode))
    assert r.exit_code == 2
    assert "chmod 600" in r.output
    assert gw.calls == [] and not gw.env.exists()  # antes de leerlo y de crear nada


def test_answers_file_stricter_than_0600_is_accepted(gw, tmp_path):
    assert onboard(write_answers(tmp_path, mode=0o400)).exit_code == 0


def test_answers_file_of_another_user_is_rejected(gw, tmp_path, monkeypatch):
    from jota_gateway.cli import onboard as onboard_mod

    path = write_answers(tmp_path)
    monkeypatch.setattr(onboard_mod.os, "getuid", lambda: path.stat().st_uid + 1)
    r = onboard(path)
    assert r.exit_code == 2 and "tuyo" in r.output
    assert gw.calls == []


def test_no_secret_in_output(gw, tmp_path):
    # Por fichero, con un fallo de verificación y con un fichero mal formado.
    path = write_answers(tmp_path, stt={"provider": "jota", "url": "localhost:9000",
                                        "token": STT_TOKEN},
                         tts={"provider": "elevenlabs", "api_key": EL_KEY})
    outputs = [onboard(path).output]
    gw.verdicts["verify_elevenlabs"] = Verdict(False, "rejected", "ElevenLabs rechazó la clave.")
    outputs.append(onboard(path).output)
    outputs.append(onboard(write_answers(tmp_path, openclaw_token=[OC_TOKEN],
                                         stt={"provider": "nope", "token": STT_TOKEN})).output)
    assert "ElevenLabs rechazó la clave." in outputs[1]
    assert "openclaw_token" in outputs[2]
    for text in outputs:
        for secret in SECRETS:
            assert secret not in text


# --- validación del fichero, terminal y configuración de partida --------------------------


@pytest.mark.parametrize(("over", "where"), [
    ({"extra": 1}, "extra"),
    ({"openclaw_port": "18789"}, "openclaw_port"),
    ({"openclaw_port": True}, "openclaw_port"),
    ({"stt": {"provider": "elevenlabs"}}, "api_key"),
    ({"stt": {"provider": "off", "url": "x:1"}}, "stt"),
    ({"tts": {"provider": "jota", "api_key": EL_KEY}}, "tts"),
    ({"first_client": {"name": "a", "type": "tele"}}, "first_client"),
])
def test_answers_file_unknown_missing_or_bad_keys(gw, tmp_path, over, where):
    r = onboard(write_answers(tmp_path, **over))
    assert r.exit_code == 2, r.output
    assert where in r.output
    assert gw.calls == [] and not gw.env.exists()
    assert EL_KEY not in r.output


def test_answers_file_missing_key(gw, tmp_path):
    path = write_answers(tmp_path)
    data = json.loads(path.read_text())
    del data["first_client"]
    path.write_text(json.dumps(data))
    r = onboard(path)
    assert r.exit_code == 2 and "first_client" in r.output


def test_answers_file_not_json(gw, tmp_path):
    path = tmp_path / "answers.json"
    path.write_text("{no es json")
    path.chmod(0o600)
    r = onboard(path)
    assert r.exit_code == 2 and "json_invalid" in r.output
    assert gw.calls == []


def test_answers_two_different_elevenlabs_keys_are_rejected(gw, tmp_path):
    r = onboard(write_answers(tmp_path, stt={"provider": "elevenlabs", "api_key": EL_KEY},
                              tts={"provider": "elevenlabs", "api_key": EL_KEY + "2"}))
    assert r.exit_code == 2 and "ELEVENLABS_API_KEY" in r.output
    assert gw.calls == []


def test_without_terminal_or_answers_exits_1(gw):
    r = run("onboard")  # CliRunner: stdin no es una terminal
    assert r.exit_code == 1
    assert "--answers" in r.output
    assert gw.calls == [] and not gw.env.exists()


def test_invalid_config_exits_before_asking(gw, tmp_path):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\nOPENCLAW_PORT=abc\n")
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 1
    assert "OPENCLAW_PORT" in r.output and "abc" not in r.output
    assert gw.calls == []
    assert isinstance(r.exception, SystemExit)  # salida limpia, sin traza


def test_real_init_from_onboard_without_its_next_step_hint(gw, tmp_path, monkeypatch):
    from jota_gateway.db import database

    monkeypatch.setattr(init_mod, "init", REAL_INIT)
    monkeypatch.setattr(database, "CREATE_DB", False)  # init lo deja en True: se restaura
    monkeypatch.setattr(database, "run_migrations", lambda: None)
    monkeypatch.setattr(database, "dispose_engine", lambda: None)
    umask = os.umask(0o022)  # init fija 077 para todo el proceso
    try:
        r = onboard(write_answers(tmp_path))
    finally:
        os.umask(umask)
    assert r.exit_code == 0, r.output
    assert "creada, con un ADMIN_TOKEN aleatorio" in r.output
    assert "Siguiente paso" not in r.output  # el asistente sigue: el paso siguiente es él
    assert installs(gw) == ["install"]


def test_answers_voice_failure_exits_1_with_detail(gw, tmp_path):
    gw.verdicts["verify_jota_tts"] = Verdict(False, "unreachable", "El servicio de TTS no responde.")
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 1
    assert "El servicio de TTS no responde." in r.output
    assert installs(gw) == []
    assert "TTS_PROVIDER" not in dotenv_values(gw.env)


def test_library_warnings_do_not_reach_the_terminal(gw, tmp_path, monkeypatch):
    async def noisy(*args):
        logging.getLogger("jota_gateway.services.tts_client").warning("TTS handshake: OSError")
        logging.getLogger("websockets.client").warning("ConnectionClosedError")
        return Verdict(False, "unreachable", "El servicio de TTS no responde.")

    monkeypatch.setattr(verify, "verify_jota_tts", noisy)
    # Sin handlers en el root (como la CLI fuera de pytest), logging.lastResort los pintaría.
    # La misma lista, vaciada y restaurada: pytest quita después sus propios handlers de ella.
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers.clear()
    try:
        r = onboard(write_answers(tmp_path))
        assert root.handlers == []  # el handler era solo para el asistente
    finally:
        root.handlers[:] = saved
    assert "El servicio de TTS no responde." in r.output
    assert "OSError" not in r.output and "ConnectionClosedError" not in r.output


# --- modo interactivo -----------------------------------------------------------------------


def test_interactive_full_flow(gw, tty):
    gw.detected = ""  # OpenClaw 2026.11.1 contesta sin versión
    gw.verdicts["verify_jota_stt"] = Verdict(False, "unreachable", "El servicio de STT no responde.")
    gw.verdicts["verify_elevenlabs"] = [
        Verdict(True, "ok", "ElevenLabs acepta la clave."),
        Verdict(False, "rejected", "ElevenLabs rechazó la clave."),
    ]
    tty.queue.extend([
        OC_TOKEN,                                   # token (OpenClaw detectado: sin dirección)
        "jota", "localhost:9000", False,            # STT propio, sin clave → falla
        "change", "elevenlabs", EL_KEY,             # cambiar respuestas → ElevenLabs
        "elevenlabs",                               # TTS: misma clave, ya verificada → falla
        "off",                                      # «ahora no»
        "Tele", "app",                              # primer cliente
    ])
    r = run("onboard")
    assert r.exit_code == 0, r.output
    assert "He encontrado OpenClaw en este equipo." in r.output
    assert "Ejecuta `openclaw config get gateway.auth.token` y pega el resultado." in r.output
    assert "Recomendado: deja este servicio accesible solo desde el gateway." in r.output
    assert dotenv_values(gw.env) == {
        "ADMIN_TOKEN": ADMIN,
        "OPENCLAW_TOKEN": OC_TOKEN, "OPENCLAW_HOST": "127.0.0.1", "OPENCLAW_PORT": "18789",
        "ELEVENLABS_API_KEY": EL_KEY, "STT_PROVIDER": "elevenlabs",
        "TTS_PROVIDER": "off",
    }
    assert ("verify_elevenlabs", "tts", EL_KEY) in gw.calls
    assert [k for k, _ in tty.asked].count("password") == 2  # la clave de ElevenLabs, una vez
    assert ("POST", "/admin/clients", {"name": "Tele", "client_type": "app"}) in gw.api.calls
    assert installs(gw) == ["install"]
    for secret in SECRETS:
        assert secret not in r.output


def test_interactive_openclaw_retry_then_cancel(gw, tty):
    gw.verdicts["verify_openclaw"] = Verdict(False, "unreachable", "OpenClaw no responde en 127.0.0.1:18789.")
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN, "retry", "cancel"])
    r = run("onboard")
    assert r.exit_code == 1
    assert NO_OPENCLAW in r.output
    assert [c[0] for c in gw.calls if isinstance(c, tuple)] == ["verify_openclaw"] * 2
    assert installs(gw) == [] and dotenv_values(gw.env) == {"ADMIN_TOKEN": ADMIN}


def test_interactive_ctrl_c_at_openclaw_says_why(gw, tty):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    r = run("onboard")  # cola vacía: la primera pregunta devuelve None (Ctrl+C)
    assert r.exit_code == 1
    assert NO_OPENCLAW in r.output


def test_interactive_ctrl_c_while_verifying_openclaw_says_why(gw, tty):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    gw.verdicts["verify_openclaw"] = KeyboardInterrupt()  # Ctrl+C durante la espera (hasta 35 s)
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN])
    r = run("onboard")
    assert r.exit_code == 1
    assert NO_OPENCLAW in r.output


def test_interactive_ctrl_c_at_voice_keeps_openclaw(gw, tty):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN])  # y Ctrl+C en el STT
    r = run("onboard")
    assert r.exit_code == 1
    env = dotenv_values(gw.env)
    assert env["OPENCLAW_TOKEN"] == OC_TOKEN and "STT_PROVIDER" not in env
    assert installs(gw) == []


def test_menu_entry_runs_onboard(monkeypatch, tty):
    calls = []
    monkeypatch.setattr(menu, "interactive", lambda: True)
    monkeypatch.setattr(menu, "_invoke", lambda ctx, argv: calls.append(argv))
    tty.queue.extend(["onboard", "exit"])
    assert run().exit_code == 0
    assert calls == [["onboard"]]
