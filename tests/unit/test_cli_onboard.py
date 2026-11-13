"""`fogar-gw onboard`: asistente de instalación (instalador mínimo, Task 5).

Nunca toca la red, systemd ni el ~/.config real: `verify.*`, `service.*`, `init`,
`doctor.run_checks` y la API de /admin van parcheados; la base y XDG_CONFIG_HOME son temporales.
"""

import io
import json
import logging
import os
from types import SimpleNamespace

import pytest
import questionary
from click.testing import CliRunner
from dotenv import dotenv_values

from fogar_gateway.cli import access as cli_access
from fogar_gateway.cli import app, doctor, menu, output, service, verify
from fogar_gateway.cli import init as init_mod
from fogar_gateway.cli import onboard as onboard_mod
from fogar_gateway.cli.verify import Verdict
from tests.fakes.cli_api import FakeApi

ADMIN = "admin-token-largo-0123456789"
OC_TOKEN = "oc-token-secreto-abcdef"
STT_TOKEN = "stt-token-secreto-456"
TTS_TOKEN = "tts-token-secreto-123"
EL_KEY = "el-clave-secreta-xyz"
CLIENT_KEY = "k" * 43
CLIENT = {"id": "aaaa1111-0000", "name": "smoke", "key_fp": "1a2b3c4d"}
SECRETS = (OC_TOKEN, STT_TOKEN, TTS_TOKEN, EL_KEY)
NO_OPENCLAW = "Sin OpenClaw no se instala el servicio. Vuelve a ejecutar `fogar-gw onboard`."
ENV = ("ADMIN_TOKEN", "OPENCLAW_HOST", "OPENCLAW_PORT", "OPENCLAW_TOKEN", "STT_PROVIDER",
       "TTS_PROVIDER", "TRANSCRIBER_WS_URL", "TRANSCRIBER_TOKEN", "TTS_WS_URL", "TTS_TOKEN",
       "ELEVENLABS_API_KEY", "GATEWAY_HOST", "FOGAR_GW_TOKEN", "FOGAR_GW_URL")
REAL_INIT = init_mod.init
REAL_ACCESS_STEP = cli_access.access_step
LAN_IP = "192.0.2.10"
DEFAULT = object()  # en la cola de `tty`: acepta el valor por defecto de la pregunta


@pytest.fixture()
def gw(monkeypatch, tmp_path, use_api):
    """Base sin config.env (con la BD), unidad en un XDG temporal y todo lo externo parcheado."""
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setenv("FOGAR_GATEWAY_HOME", str(base))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    st = SimpleNamespace(calls=[], verdicts={}, detected=None, env=base / "config.env",
                         db=base / "data" / "gateway.db")
    st.db.parent.mkdir()
    st.db.touch()

    def fake_init():  # como el real: crea lo que falta y no toca lo que existe
        st.calls.append("init")
        if not st.env.exists():
            st.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
        st.db.touch()

    def fake_install():
        from fogar_gateway.cli.api import ApiError

        st.calls.append("install")
        if not st.db.exists():  # como el real: sin BD no instala
            raise ApiError("No existe la base de datos.", 1)
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
    monkeypatch.setattr(service, "require_systemd", lambda: None)
    monkeypatch.setattr(onboard_mod, "_lan_ip", lambda: LAN_IP)  # sin socket real
    monkeypatch.setattr(doctor, "run_checks", lambda url, token: [
        {"level": "fail", "check": "orquestador", "detail": "aviso del doctor"}])
    for name in ("verify_openclaw", "verify_fogar_stt", "verify_fogar_tts", "verify_elevenlabs"):
        monkeypatch.setattr(verify, name, verifier(name))
    monkeypatch.setattr(verify, "detect_openclaw", detect)
    # Con terminal, el asistente acaba esperando al dashboard: aquí ya hay uno conectado.
    st.api = use_api(FakeApi({("GET", "/admin/clients"): [],
                              ("POST", "/admin/clients"): {**CLIENT, "client_key": CLIENT_KEY},
                              ("GET", "/admin/state"): {"gateway": {"dashboards": 1}}}))
    monkeypatch.setattr(onboard_mod, "_enter_pressed", lambda timeout: True)  # nunca espera
    # El paso de acceso tiene sus tests (test_cli_access_setup.py): aquí solo se anota, con
    # lo que ya había pasado (para comprobar el orden).
    st.access = []
    monkeypatch.setattr(cli_access, "access_step",
                        lambda out, answers, settings: st.access.append((answers, list(st.calls))))
    return st


@pytest.fixture()
def tty(monkeypatch):
    """Terminal interactiva: cada pregunta de questionary saca la siguiente respuesta de la
    cola (None = Ctrl+C, DEFAULT = su valor por defecto) y queda anotada en `asked` como
    (tipo, mensaje, kwargs). Un `select` solo acepta uno de sus valores."""
    st = SimpleNamespace(queue=[], asked=[])

    def question(kind):
        class _Q:
            def __init__(self, message, *args, **kwargs):
                self.kwargs = kwargs
                st.asked.append((kind, message, kwargs))

            def ask(self):
                answer = st.queue.pop(0) if st.queue else None
                if answer is DEFAULT:
                    return self.kwargs["default"]
                if kind == "select" and answer is not None:
                    values = [c.value for c in self.kwargs["choices"]]
                    assert answer in values, (answer, values)
                return answer
        return _Q

    for kind in ("select", "text", "password", "confirm"):
        monkeypatch.setattr(questionary, kind, question(kind))
    monkeypatch.setattr(output, "_stdin_isatty", lambda: True)
    return st


def write_answers(tmp_path, mode=0o600, **over):
    data = {"openclaw_host": "127.0.0.1", "openclaw_port": 18789, "openclaw_token": OC_TOKEN,
            "stt": {"provider": "off"},
            "tts": {"provider": "fogar", "url": "localhost:8005", "token": TTS_TOKEN},
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


def asked(tty, kind, part):
    """kwargs de las preguntas de ese tipo cuyo mensaje contiene `part`."""
    return [kw for k, message, kw in tty.asked if k == kind and part in message]


def titles(kwargs):
    return [c.title.lower() for c in kwargs["choices"]]


def assert_cancelled(r):
    # Cancelación limpia (click.Abort → «Aborted!», salida 1), no un fallo con traza.
    assert r.exit_code == 1 and isinstance(r.exception, SystemExit), r.exception
    assert "Aborted!" in r.output


# --- los tests de la brief -----------------------------------------------------------------


def test_onboard_happy_path_answers_file(gw, tmp_path):
    path = write_answers(tmp_path, stt={"provider": "fogar", "url": "localhost:9000"},
                         tts={"provider": "elevenlabs", "api_key": EL_KEY})
    r = onboard(path)
    assert r.exit_code == 0, r.output
    assert dotenv_values(gw.env) == {
        "ADMIN_TOKEN": ADMIN,
        "OPENCLAW_TOKEN": OC_TOKEN, "OPENCLAW_HOST": "127.0.0.1", "OPENCLAW_PORT": "18789",
        "TRANSCRIBER_WS_URL": "localhost:9000", "TRANSCRIBER_TOKEN": "", "STT_PROVIDER": "fogar",
        "ELEVENLABS_API_KEY": EL_KEY, "TTS_PROVIDER": "elevenlabs",
    }
    assert gw.calls == [
        "init",
        ("verify_openclaw", "127.0.0.1", 18789, OC_TOKEN),
        ("verify_fogar_stt", "localhost:9000", ""),
        ("verify_elevenlabs", "tts", EL_KEY),
        "install",
    ]
    assert ("POST", "/admin/clients", {"name": "smoke", "client_type": "web"}) in gw.api.calls
    assert r.output.count(CLIENT_KEY) == 1  # la clave, una sola vez
    assert "Recomendado: deja este servicio accesible solo desde el gateway." in r.output
    assert "aviso del doctor" in r.output  # el doctor solo informa: no cambia el código
    assert f"ws://{LAN_IP}:8004/ws/stream" in r.output


def test_onboard_openclaw_unverified_exits_without_install(gw, tmp_path):
    gw.verdicts["verify_openclaw"] = Verdict(False, "rejected", "OpenClaw rechazó el token.")
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 1
    assert "OpenClaw rechazó el token." in r.output and NO_OPENCLAW in r.output
    assert r.output.index("OpenClaw rechazó el token.") < r.output.index(NO_OPENCLAW)
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
    path = write_answers(tmp_path, stt={"provider": "fogar", "url": "localhost:9000",
                                        "token": STT_TOKEN})
    gw.verdicts["verify_fogar_stt"] = KeyboardInterrupt()
    r = onboard(path)
    assert_cancelled(r)
    env = dotenv_values(gw.env)
    assert env["OPENCLAW_TOKEN"] == OC_TOKEN and env["OPENCLAW_PORT"] == "18789"
    assert "STT_PROVIDER" not in env and "TRANSCRIBER_TOKEN" not in env
    assert not service.unit_path().exists() and installs(gw) == []

    del gw.verdicts["verify_fogar_stt"]
    r = onboard(path)
    assert r.exit_code == 0, r.output
    env = dotenv_values(gw.env)
    assert env["STT_PROVIDER"] == "fogar" and env["TRANSCRIBER_TOKEN"] == STT_TOKEN
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
    from fogar_gateway.cli import onboard as onboard_mod

    path = write_answers(tmp_path)
    monkeypatch.setattr(onboard_mod.os, "getuid", lambda: path.stat().st_uid + 1)
    r = onboard(path)
    assert r.exit_code == 2 and "tuyo" in r.output
    assert gw.calls == []


def test_no_secret_in_output(gw, tmp_path):
    # Por fichero, con un fallo de verificación y con un fichero mal formado.
    path = write_answers(tmp_path, stt={"provider": "fogar", "url": "localhost:9000",
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
    ({"tts": {"provider": "fogar", "api_key": EL_KEY}}, "tts"),
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
    del data["openclaw_token"]
    path.write_text(json.dumps(data))
    r = onboard(path)
    assert r.exit_code == 2 and "openclaw_token" in r.output


def test_answers_file_without_first_client_creates_none(gw, tmp_path):
    path = write_answers(tmp_path)
    data = json.loads(path.read_text())
    del data["first_client"]
    path.write_text(json.dumps(data))
    r = onboard(path)
    assert r.exit_code == 0, r.output
    assert [c for c in gw.api.calls if c[0] == "POST"] == []


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
    from fogar_gateway.db import database

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
    gw.verdicts["verify_fogar_tts"] = Verdict(False, "unreachable", "El servicio de TTS no responde.")
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 1
    assert "El servicio de TTS no responde." in r.output
    assert installs(gw) == []
    assert "TTS_PROVIDER" not in dotenv_values(gw.env)


def test_library_warnings_do_not_reach_the_terminal(gw, tmp_path, monkeypatch):
    async def noisy(*args):
        logging.getLogger("fogar_gateway.services.tts_client").warning("TTS handshake: OSError")
        logging.getLogger("websockets.client").warning("ConnectionClosedError")
        return Verdict(False, "unreachable", "El servicio de TTS no responde.")

    monkeypatch.setattr(verify, "verify_fogar_tts", noisy)
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
    gw.verdicts["verify_fogar_stt"] = Verdict(False, "unreachable", "El servicio de STT no responde.")
    gw.verdicts["verify_elevenlabs"] = [
        Verdict(True, "ok", "ElevenLabs acepta la clave."),
        Verdict(False, "rejected", "ElevenLabs rechazó la clave."),
    ]
    tty.queue.extend([
        OC_TOKEN,                                   # token (OpenClaw detectado: sin dirección)
        "fogar", "localhost:9000", False,            # STT propio, sin clave → falla
        "change", "elevenlabs", EL_KEY,             # cambiar respuestas → ElevenLabs
        "elevenlabs",                               # TTS: misma clave, ya verificada → falla
        "off",                                      # «ahora no»
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
    assert [k for k, _, _ in tty.asked].count("password") == 2  # la clave de ElevenLabs, una vez
    assert [c for c in gw.api.calls if c[0] == "POST"] == []  # el primer cliente, en la web
    assert "Conectado: ya puedes seguir en el dashboard." in r.output
    assert installs(gw) == ["install"]
    # El paso de acceso, una vez y preguntando (None): tras las voces y antes del servicio.
    (access_answers, before), = gw.access
    assert access_answers is None
    assert ("verify_elevenlabs", "tts", EL_KEY) in before and "install" not in before
    for secret in SECRETS:
        assert secret not in r.output
    menus = asked(tty, "select", "¿Qué hago?")
    assert [titles(kw) for kw in menus] == [["reintentar", "cambiar respuestas", "ahora no"]] * 2


def test_interactive_openclaw_retry_then_cancel(gw, tty):
    gw.verdicts["verify_openclaw"] = Verdict(False, "unreachable", "OpenClaw no responde en 127.0.0.1:18789.")
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN, "retry", "cancel"])
    r = run("onboard")
    assert r.exit_code == 1
    assert NO_OPENCLAW in r.output
    assert titles(asked(tty, "select", "¿Qué hago?")[0]) == [
        "reintentar", "cambiar respuestas", "cancelar"]
    assert [c[0] for c in gw.calls if isinstance(c, tuple)] == ["verify_openclaw"] * 2
    assert installs(gw) == [] and dotenv_values(gw.env) == {"ADMIN_TOKEN": ADMIN}


def test_interactive_ctrl_c_at_openclaw_says_why(gw, tty):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    r = run("onboard")  # cola vacía: la primera pregunta devuelve None (Ctrl+C)
    assert r.exit_code == 1 and isinstance(r.exception, SystemExit)
    assert NO_OPENCLAW in r.output and "Traceback" not in r.output


def test_interactive_ctrl_c_while_verifying_openclaw_says_why(gw, tty):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    gw.verdicts["verify_openclaw"] = KeyboardInterrupt()  # Ctrl+C durante la espera (hasta 35 s)
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN])
    r = run("onboard")
    assert r.exit_code == 1 and isinstance(r.exception, SystemExit)
    assert NO_OPENCLAW in r.output and "Aborted!" not in r.output


def test_interactive_ctrl_c_at_voice_keeps_openclaw(gw, tty):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN])  # y Ctrl+C en el STT
    r = run("onboard")
    assert_cancelled(r)
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


# --- ronda de arreglos 1 --------------------------------------------------------------------


def test_interactive_defaults_and_labels(gw, tty):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    tty.queue.extend([
        DEFAULT, DEFAULT, OC_TOKEN,                 # OpenClaw no detectado: host y puerto
        "fogar", DEFAULT, DEFAULT,                   # STT propio, dirección y «¿usa clave?»
        "fogar", DEFAULT, DEFAULT,                   # TTS igual
    ])
    r = run("onboard")
    assert r.exit_code == 0, r.output
    assert [kw["default"] for kw in asked(tty, "text", "OpenClaw")] == ["127.0.0.1", "18789"]
    assert [kw["default"] for kw in asked(tty, "text", "Dirección")] == [
        "localhost:9000", "localhost:8005"]
    assert [kw["default"] for kw in asked(tty, "confirm", "¿Usa clave?")] == [False, False]
    for kw in asked(tty, "select", "¿qué servicio?"):
        assert titles(kw) == ["servicio propio", "elevenlabs", "ahora no"]
    assert ("verify_openclaw", "127.0.0.1", 18789, OC_TOKEN) in gw.calls
    assert ("verify_fogar_stt", "localhost:9000", "") in gw.calls
    assert ("verify_fogar_tts", "localhost:8005", "") in gw.calls
    env = dotenv_values(gw.env)
    assert env["TRANSCRIBER_WS_URL"] == "localhost:9000" and env["TTS_WS_URL"] == "localhost:8005"
    assert r.output.count("Recomendado: deja este servicio accesible solo desde el gateway.") == 2


def test_answers_fogar_without_url_writes_the_default(gw, tmp_path):
    r = onboard(write_answers(tmp_path, stt={"provider": "fogar"}, tts={"provider": "fogar"}))
    assert r.exit_code == 0, r.output
    env = dotenv_values(gw.env)
    assert env["TRANSCRIBER_WS_URL"] == "localhost:9000" and env["TTS_WS_URL"] == "localhost:8005"
    assert env["TRANSCRIBER_TOKEN"] == "" and env["TTS_TOKEN"] == ""


OLD_KEY = "el-clave-vieja-del-tts"
NEW_KEY = "el-clave-nueva-del-stt"


def _tts_already_on_elevenlabs(gw):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\nELEVENLABS_API_KEY={OLD_KEY}\nTTS_PROVIDER=elevenlabs\n")


def test_new_stt_key_is_verified_for_an_elevenlabs_tts_interactive(gw, tty):
    _tts_already_on_elevenlabs(gw)
    gw.api.responses[("GET", "/admin/clients")] = [CLIENT]
    gw.verdicts["verify_elevenlabs"] = [
        Verdict(True, "ok", "ElevenLabs acepta la clave."),                 # STT
        Verdict(False, "rejected", "ElevenLabs rechazó la clave."),         # la misma, para el TTS
    ]
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN,
                      "elevenlabs", NEW_KEY, "off",                         # STT → «ahora no»
                      "off"])                                               # TTS
    r = run("onboard")
    assert r.exit_code == 0, r.output
    assert [c for c in gw.calls if c[0] == "verify_elevenlabs"] == [
        ("verify_elevenlabs", "stt", NEW_KEY), ("verify_elevenlabs", "tts", NEW_KEY)]
    assert "El TTS ya usa ElevenLabs" in r.output
    env = dotenv_values(gw.env)
    assert env["ELEVENLABS_API_KEY"] == OLD_KEY and env["STT_PROVIDER"] == "off"
    assert NEW_KEY not in r.output and OLD_KEY not in r.output


def test_new_stt_key_is_verified_for_an_elevenlabs_tts_answers(gw, tmp_path):
    _tts_already_on_elevenlabs(gw)
    gw.verdicts["verify_elevenlabs"] = [
        Verdict(True, "ok", "ElevenLabs acepta la clave."),
        Verdict(False, "rejected", "ElevenLabs rechazó la clave."),
    ]
    eleven = {"provider": "elevenlabs", "api_key": NEW_KEY}
    r = onboard(write_answers(tmp_path, stt=eleven, tts=eleven))
    assert r.exit_code == 1
    assert "El TTS ya usa ElevenLabs" in r.output
    env = dotenv_values(gw.env)
    assert env["ELEVENLABS_API_KEY"] == OLD_KEY and "STT_PROVIDER" not in env  # nada escrito
    assert installs(gw) == []


def test_new_stt_key_is_verified_for_tts_even_if_answers_move_tts_to_fogar(gw, tmp_path):
    # El TTS de fogar puede fallar después: config.env se quedaría con el TTS en ElevenLabs y B.
    _tts_already_on_elevenlabs(gw)
    gw.verdicts["verify_elevenlabs"] = [
        Verdict(True, "ok", "ElevenLabs acepta la clave."),
        Verdict(False, "rejected", "ElevenLabs rechazó la clave."),
    ]
    r = onboard(write_answers(tmp_path, stt={"provider": "elevenlabs", "api_key": NEW_KEY}))
    assert r.exit_code == 1 and "El TTS ya usa ElevenLabs" in r.output
    env = dotenv_values(gw.env)
    assert env["ELEVENLABS_API_KEY"] == OLD_KEY and "STT_PROVIDER" not in env
    assert not any(c[0] == "verify_fogar_tts" for c in gw.calls if isinstance(c, tuple))


def test_tts_check_passes_then_fogar_tts_fails_leaves_a_verified_key(gw, tmp_path):
    _tts_already_on_elevenlabs(gw)
    gw.verdicts["verify_fogar_tts"] = Verdict(False, "unreachable", "El servicio de TTS no responde.")
    r = onboard(write_answers(tmp_path, stt={"provider": "elevenlabs", "api_key": NEW_KEY}))
    assert r.exit_code == 1 and "El servicio de TTS no responde." in r.output
    assert [c for c in gw.calls if c[0] == "verify_elevenlabs"] == [
        ("verify_elevenlabs", "stt", NEW_KEY), ("verify_elevenlabs", "tts", NEW_KEY)]
    env = dotenv_values(gw.env)  # el TTS sigue en ElevenLabs, con una clave verificada para él
    assert env["ELEVENLABS_API_KEY"] == NEW_KEY and env["TTS_PROVIDER"] == "elevenlabs"


def test_no_tts_check_when_the_answers_replace_the_tts(gw, tmp_path):
    _tts_already_on_elevenlabs(gw)
    r = onboard(write_answers(tmp_path, stt={"provider": "elevenlabs", "api_key": NEW_KEY},
                              tts={"provider": "off"}))
    assert r.exit_code == 0, r.output
    assert [c for c in gw.calls if c[0] == "verify_elevenlabs"] == [
        ("verify_elevenlabs", "stt", NEW_KEY)]
    env = dotenv_values(gw.env)
    assert env["ELEVENLABS_API_KEY"] == NEW_KEY and env["TTS_PROVIDER"] == "off"


def test_no_tts_check_when_the_key_does_not_change(gw, tmp_path):
    _tts_already_on_elevenlabs(gw)
    eleven = {"provider": "elevenlabs", "api_key": OLD_KEY}
    assert onboard(write_answers(tmp_path, stt=eleven, tts=eleven)).exit_code == 0
    assert [c[1] for c in gw.calls if c[0] == "verify_elevenlabs"] == ["stt", "tts"]


@pytest.mark.parametrize("yes", [True, False])
def test_interactive_rerun_asks_before_restarting(gw, tty, yes):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    service.unit_path().parent.mkdir(parents=True)
    service.unit_path().write_text("[Unit]\n")
    gw.api.responses[("GET", "/admin/clients")] = [CLIENT]
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN, "off", "off", yes])
    r = run("onboard")
    assert r.exit_code == 0, r.output
    (kw,) = asked(tty, "confirm", "sesiones abiertas")
    assert kw["default"] is True
    assert installs(gw) == (["restart"] if yes else [])
    if not yes:
        assert "fogar-gw restart" in r.output


def test_answers_rerun_restarts_without_asking(gw, tty, tmp_path):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    service.unit_path().parent.mkdir(parents=True)
    service.unit_path().write_text("[Unit]\n")
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 0, r.output
    assert tty.asked == [] and installs(gw) == ["restart"]


def test_without_systemd_fails_before_creating_or_asking(gw, tty, monkeypatch):
    from fogar_gateway.cli.api import ApiError

    def no_systemd():
        raise ApiError("Este comando necesita Linux con systemd.", 1)

    monkeypatch.setattr(service, "require_systemd", no_systemd)
    r = run("onboard")
    assert r.exit_code == 1 and "systemd" in r.output
    assert gw.calls == [] and tty.asked == [] and not gw.env.exists()


@pytest.mark.parametrize("name", ["   ", "x" * 201])
def test_answers_first_client_name_follows_server_rules(gw, tmp_path, name):
    r = onboard(write_answers(tmp_path, first_client={"name": name, "type": "web"}))
    assert r.exit_code == 2 and "first_client.name" in r.output
    assert gw.calls == []  # antes de instalar nada


def test_interactive_does_not_ask_for_a_first_client(gw, tty):
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN, "off", "off"])
    r = run("onboard")
    assert r.exit_code == 0, r.output
    assert asked(tty, "text", "cliente") == [] and tty.queue == []
    assert [c for c in gw.api.calls if c[0] == "POST"] == []
    assert "Empareja tu primer aparato desde Clientes, en el dashboard." in r.output


@pytest.mark.parametrize(("host", "shown"), [
    ("0.0.0.0", LAN_IP), ("::", LAN_IP), ("", LAN_IP), ("192.168.1.19", "192.168.1.19"),
    ("fd00::19", "[fd00::19]"), ("greenhouse.lan", "greenhouse.lan"),
])
def test_dashboard_and_client_address_follow_gateway_host(gw, tmp_path, host, shown):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\nGATEWAY_HOST={host}\n")
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 0, r.output
    assert f"Tu dashboard: http://{shown}:8004/" in r.output
    assert f"ws://{shown}:8004/ws/stream" in r.output


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.1.1", "::1", "localhost"])
def test_loopback_gateway_gives_no_lan_url(gw, tmp_path, host):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\nGATEWAY_HOST={host}\n")
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 0, r.output
    assert "solo escucha en este equipo" in r.output and "ssh -L 8004:127.0.0.1:8004" in r.output
    assert "Tu dashboard:" not in r.output and "ws://" not in r.output


def test_without_lan_ip_never_prints_loopback(gw, tmp_path, monkeypatch):
    monkeypatch.setattr(onboard_mod, "_lan_ip", lambda: None)
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 0, r.output
    assert "http://<IP de este equipo>:8004/" in r.output and "hostname -I" in r.output
    assert "127.0.0.1:8004" not in r.output


def test_answers_never_show_the_admin_token(gw, tmp_path, monkeypatch):
    monkeypatch.setattr(output, "_isatty", lambda: True)  # ni siquiera en una terminal
    monkeypatch.setenv("NO_COLOR", "1")
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 0, r.output
    assert ADMIN not in r.output
    assert f"{gw.env} (ADMIN_TOKEN)" in r.output and "`fogar-gw token`" in r.output
    assert "No lo compartas" in r.output
    assert ("GET", "/admin/state", None) not in gw.api.calls  # sin terminal no espera


@pytest.mark.parametrize("terminal", [False, True])
def test_interactive_shows_the_token_only_on_a_terminal(gw, tty, monkeypatch, terminal):
    if terminal:  # el stdout de CliRunner no es una terminal
        monkeypatch.setattr(output, "_isatty", lambda: True)
        monkeypatch.setenv("NO_COLOR", "1")  # texto plano: la salida de rich no la captura CliRunner
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN, "off", "off"])
    r = run("onboard")
    assert r.exit_code == 0, r.output
    assert (f"Token: {ADMIN}" in r.output) is terminal
    assert (ADMIN in r.output) is terminal
    assert f"Tu dashboard: http://{LAN_IP}:8004/" in r.output and "`fogar-gw token`" in r.output


@pytest.mark.parametrize("how", ["enter", "ctrl_c"])
def test_interactive_wait_for_the_dashboard_can_be_skipped(gw, tty, monkeypatch, how):
    gw.api.responses[("GET", "/admin/state")] = {"gateway": {"dashboards": 0}}
    polls = []

    def pressed(timeout):
        polls.append(timeout)
        if how == "ctrl_c":
            raise KeyboardInterrupt
        return len(polls) == 3  # Enter a la tercera vuelta

    monkeypatch.setattr(onboard_mod, "_enter_pressed", pressed)
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN, "off", "off"])
    r = run("onboard")
    assert r.exit_code == 0, r.output
    assert "Conectado" not in r.output
    assert f"Sin esperar: entra cuando quieras en http://{LAN_IP}:8004/." in r.output
    assert len(polls) == (3 if how == "enter" else 1)


def test_interactive_waits_until_a_dashboard_connects(gw, tty, monkeypatch):
    states = [None, {"gateway": {"dashboards": 0}}, {"gateway": {"dashboards": 1}}]

    class Api:
        calls = gw.api.calls

        def request(self, method, path, body=None):
            if path != "/admin/state":
                return gw.api.request(method, path, body)
            state = states.pop(0)
            if state is None:
                from fogar_gateway.cli.api import ApiError
                raise ApiError("El gateway no responde.", 1)  # aún arrancando: se reintenta
            return state

    from fogar_gateway.cli import api
    monkeypatch.setattr(api, "from_context", lambda ctx: Api())
    monkeypatch.setattr(onboard_mod, "_enter_pressed", lambda timeout: False)
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN, "off", "off"])
    r = run("onboard")
    assert r.exit_code == 0, r.output
    assert states == [] and "Conectado: ya puedes seguir en el dashboard." in r.output


def test_lan_ip_falls_back_to_loopback(monkeypatch):
    import socket

    class NoRoute:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def connect(self, address):
            raise OSError("Network is unreachable")

    monkeypatch.setattr(socket, "socket", NoRoute)  # sin `gw`: `_lan_ip` sin parchear
    assert onboard_mod._lan_ip() is None


# --- ola final: init sin BD y «Dejar como está» -------------------------------------------


def test_onboard_runs_init_when_only_the_db_is_missing(gw, tmp_path):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\nGATEWAY_HOST=127.0.0.1\n")
    gw.db.unlink()
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 0, r.output
    assert gw.calls.count("init") == 1 and installs(gw) == ["install"]
    assert dotenv_values(gw.env)["GATEWAY_HOST"] == "127.0.0.1"  # init no toca lo que existe


def test_onboard_with_config_and_db_does_not_run_init(gw, tmp_path):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    assert onboard(write_answers(tmp_path)).exit_code == 0
    assert "init" not in gw.calls


CONFIGURED = (f"ADMIN_TOKEN={ADMIN}\nOPENCLAW_TOKEN={OC_TOKEN}\nOPENCLAW_HOST=127.0.0.1\n"
              "OPENCLAW_PORT=18789\nTRANSCRIBER_WS_URL=stt.lan:9000\nTRANSCRIBER_TOKEN=\n"
              "STT_PROVIDER=fogar\nTTS_PROVIDER=off\n")


def _configured_rerun(gw, env=CONFIGURED):
    """Un relanzamiento: config.env ya escrito, servicio instalado y clientes creados."""
    gw.env.write_text(env)
    service.unit_path().parent.mkdir(parents=True)
    service.unit_path().write_text("[Unit]\n")
    gw.api.responses[("GET", "/admin/clients")] = [CLIENT]


def test_interactive_rerun_keeps_everything_by_default(gw, tty):
    _configured_rerun(gw)
    tty.queue.extend([DEFAULT, DEFAULT, DEFAULT, True])  # OpenClaw, STT, TTS y reinicio
    r = run("onboard")
    assert r.exit_code == 0, r.output
    (oc,) = asked(tty, "select", "OpenClaw")
    assert titles(oc)[0] == "dejar como está (127.0.0.1:18789)" and oc["default"] == "keep"
    stt, tts = asked(tty, "select", "¿qué servicio?")
    assert titles(stt) == ["dejar como está (servicio propio)", "servicio propio", "elevenlabs",
                           "ahora no"]
    assert titles(tts)[0] == "dejar como está (desactivado)"
    assert stt["default"] == tts["default"] == "keep"
    assert not any(isinstance(c, tuple) for c in gw.calls)  # ni verifica ni busca OpenClaw
    assert dotenv_values(gw.env) == dotenv_values(stream=io.StringIO(CONFIGURED))
    assert asked(tty, "password", "") == []  # no pide ningún token
    assert installs(gw) == ["restart"]


def test_interactive_rerun_keeps_openclaw_and_changes_stt(gw, tty):
    _configured_rerun(gw)
    tty.queue.extend(["keep", "elevenlabs", EL_KEY, "keep", True])
    r = run("onboard")
    assert r.exit_code == 0, r.output
    assert [c for c in gw.calls if isinstance(c, tuple)] == [("verify_elevenlabs", "stt", EL_KEY)]
    env = dotenv_values(gw.env)
    assert env["OPENCLAW_TOKEN"] == OC_TOKEN and env["STT_PROVIDER"] == "elevenlabs"
    assert env["ELEVENLABS_API_KEY"] == EL_KEY and env["TTS_PROVIDER"] == "off"
    assert installs(gw) == ["restart"]


def test_interactive_rerun_can_change_openclaw(gw, tty):
    _configured_rerun(gw)
    tty.queue.extend(["change", "127.0.0.1", "18790", "oc-otro-token", "keep", "keep", True])
    r = run("onboard")
    assert r.exit_code == 0, r.output
    assert ("verify_openclaw", "127.0.0.1", 18790, "oc-otro-token") in gw.calls
    assert dotenv_values(gw.env)["OPENCLAW_PORT"] == "18790"


def test_no_keep_option_on_a_first_run(gw, tty):
    gw.env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    tty.queue.extend(["127.0.0.1", "18789", OC_TOKEN, "off", "off"])
    assert run("onboard").exit_code == 0
    assert asked(tty, "select", "OpenClaw") == []
    for kw in asked(tty, "select", "¿qué servicio?"):
        assert "keep" not in [c.value for c in kw["choices"]]


def test_answers_rerun_never_keeps(gw, tmp_path):
    gw.env.write_text(CONFIGURED)
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 0, r.output
    assert [c[0] for c in gw.calls if isinstance(c, tuple)] == ["verify_openclaw",
                                                                "verify_fogar_tts"]
    env = dotenv_values(gw.env)
    assert env["STT_PROVIDER"] == "off" and env["TTS_PROVIDER"] == "fogar"


def test_new_tts_key_is_verified_for_a_kept_elevenlabs_stt(gw, tty):
    # STT se queda en ElevenLabs con la clave vieja; el TTS trae otra: el STT pasaría a usarla.
    _configured_rerun(gw, f"ADMIN_TOKEN={ADMIN}\nOPENCLAW_TOKEN={OC_TOKEN}\n"
                          f"ELEVENLABS_API_KEY={OLD_KEY}\nSTT_PROVIDER=elevenlabs\n")
    gw.verdicts["verify_elevenlabs"] = [
        Verdict(True, "ok", "ElevenLabs acepta la clave."),                 # TTS
        Verdict(False, "rejected", "ElevenLabs rechazó la clave."),         # la misma, para el STT
    ]
    tty.queue.extend(["keep", "keep", "elevenlabs", NEW_KEY, "off", True])
    r = run("onboard")
    assert r.exit_code == 0, r.output
    assert [c for c in gw.calls if c[0] == "verify_elevenlabs"] == [
        ("verify_elevenlabs", "tts", NEW_KEY), ("verify_elevenlabs", "stt", NEW_KEY)]
    assert "El STT ya usa ElevenLabs" in r.output
    env = dotenv_values(gw.env)
    assert env["ELEVENLABS_API_KEY"] == OLD_KEY and env["TTS_PROVIDER"] == "off"
    assert NEW_KEY not in r.output and OLD_KEY not in r.output


# --- el paso «¿Cómo vas a entrar a Fogar?» (Task 8) ----------------------------------------


def test_onboard_answers_without_access_skips_step(gw, tmp_path):
    r = onboard(write_answers(tmp_path))
    assert r.exit_code == 0, r.output
    assert gw.access == []  # el CI no cambia
    assert "PUBLIC_URLS" not in dotenv_values(gw.env)


def test_onboard_answers_with_public_urls(gw, tmp_path, monkeypatch):
    monkeypatch.setattr(cli_access, "access_step", REAL_ACCESS_STEP)
    monkeypatch.delenv("PUBLIC_URLS", raising=False)
    path = write_answers(tmp_path, access={"public_urls": ["https://fogar.example.com"]})
    r = onboard(path)
    assert r.exit_code == 0, r.output
    assert dotenv_values(gw.env)["PUBLIC_URLS"] == "https://fogar.example.com/"
    assert "Haz que apunte a http://localhost:8004" in r.output
    # tras las voces y antes del servicio
    assert r.output.index("Haz que apunte") < r.output.index("aviso del doctor")


def test_onboard_answers_access_step_runs_between_voice_and_service(gw, tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(cli_access, "access_step",
                        lambda out, answers, settings: order.append(("access", list(gw.calls))))
    r = onboard(write_answers(tmp_path, access={"tailscale_serve": True}))
    assert r.exit_code == 0, r.output
    (_, before), = order
    assert ("verify_fogar_tts", "localhost:8005", TTS_TOKEN) in before
    assert "install" not in before and "install" in gw.calls


def test_onboard_answers_tailscale_permission_never_runs_sudo(gw, tmp_path, monkeypatch):
    from fogar_gateway import access as access_mod

    monkeypatch.setattr(cli_access, "access_step", REAL_ACCESS_STEP)
    monkeypatch.setattr(access_mod, "tailscale_status",
                        lambda exe="tailscale": {"BackendState": "Running", "Self": {}})
    monkeypatch.setattr(access_mod, "serve_status", lambda exe="tailscale": {})
    tried = []
    monkeypatch.setattr(cli_access, "enable_serve",
                        lambda port, gw_port, exe="tailscale": tried.append(port) or ("permiso", ""))
    monkeypatch.setattr(cli_access.subprocess, "run",
                        lambda *a, **k: pytest.fail("sudo sin terminal"))
    r = onboard(write_answers(tmp_path, access={"tailscale_serve": True}))
    assert r.exit_code == 0, r.output
    assert tried == [443]
    assert "sudo tailscale set --operator=$USER" in r.output


@pytest.mark.parametrize("url", ["", "   "])
def test_onboard_answers_rejects_empty_public_url(gw, tmp_path, url):
    r = onboard(write_answers(tmp_path, access={"public_urls": [url]}))
    assert r.exit_code == 2
    assert "access.public_urls" in r.output and "value_error" in r.output
    assert gw.calls == [] and gw.access == []


def test_onboard_answers_rejects_http_public_url(gw, tmp_path):
    url = "http://usuario:secreto@fogar.example.com"
    r = onboard(write_answers(tmp_path, access={"public_urls": [url]}))
    assert r.exit_code == 2
    assert "access.public_urls" in r.output and "value_error" in r.output
    assert "secreto" not in r.output and "fogar.example.com" not in r.output
    assert gw.calls == []  # nada antes de validar el fichero entero
