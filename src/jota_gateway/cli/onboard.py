"""
onboard.py
~~~~~~~~~~
`jota-gw onboard`: el asistente de instalación. Pregunta lo mínimo, verifica cada proveedor
contra el servicio real (`cli/verify.py`) antes de guardarlo con `cli/config.py::_apply`,
instala o reinicia el servicio y crea el primer cliente. Con `--answers FICHERO` (JSON 600)
no pregunta nada: es el modo de `install.sh` y del CI. Cortarlo a medias deja config.env
como estaba más lo ya verificado; volver a ejecutarlo retoma.

`questionary`, `pydantic` y los verificadores se importan al usarse (`--help` < 150 ms).
"""

import asyncio
import logging
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import click

from jota_gateway import paths
from jota_gateway.cli import api
from jota_gateway.cli import config as cfg
from jota_gateway.cli.output import MUTED, Out

NO_OPENCLAW = "Sin OpenClaw no se instala el servicio. Vuelve a ejecutar `jota-gw onboard`."
TOKEN_HINT = "Ejecuta `openclaw config get gateway.auth.token` y pega el resultado."
OPEN_SERVICE = "Recomendado: deja este servicio accesible solo desde el gateway."
ELEVENLABS_KEY = "ELEVENLABS_API_KEY"
# tipo -> (nombre, ajuste de la dirección, de la clave, del proveedor, dirección por defecto)
VOICE = {
    "stt": ("STT", "TRANSCRIBER_WS_URL", "TRANSCRIBER_TOKEN", "STT_PROVIDER", "localhost:9000"),
    "tts": ("TTS", "TTS_WS_URL", "TTS_TOKEN", "TTS_PROVIDER", "localhost:8005"),
}


def _ask(kind: str, message: str, **kwargs: Any) -> Any:
    """Una pregunta de questionary; Ctrl+C (questionary devuelve None) cancela el asistente."""
    import questionary

    from jota_gateway.cli.menu import _style

    answer = getattr(questionary, kind)(message, style=_style(), **kwargs).ask()
    if answer is None:
        raise click.Abort
    return answer


def _choose(message: str, options: list[tuple[str, str]]) -> str:
    import questionary

    choices = [questionary.Choice(title, value=value) for title, value in options]
    answer: str = _ask("select", message, choices=choices)
    return answer


def _required(value: str) -> bool | str:
    return bool(value.strip()) or "Es obligatorio."


def _settle[T](out: Out, ask: Callable[[], T], check: Callable[[T], Any], interactive: bool,
               give_up: tuple[str, str]) -> T | None:
    """Pregunta y verifica hasta que pase. Sin terminal, un fallo sale con 1 y su detalle;
    con terminal, se elige reintentar, cambiar las respuestas o `give_up` (devuelve None)."""
    values = ask()
    while True:
        verdict = check(values)
        if verdict.ok:
            out.message(verdict.detail)
            return values
        if not interactive:
            raise api.ApiError(verdict.detail, 1)
        out.error(verdict.detail)
        choice = _choose("¿Qué hago?", [("Reintentar", "retry"),
                                        ("Cambiar respuestas", "change"), give_up])
        if choice == "change":
            values = ask()
        elif choice != "retry":
            return None


def _write(out: Out, values: dict[str, str]) -> None:
    # En orden: la clave de ElevenLabs antes que el proveedor que la exige (si no, `_apply`
    # vería un fallo nuevo de validación y no escribiría).
    for key, value in values.items():
        cfg._apply(out, key, value)


def _openclaw(out: Out, answers: Any) -> None:
    from jota_gateway.cli import verify

    found = None if answers else asyncio.run(verify.detect_openclaw())
    if found is not None:  # "" = encontrado, sin versión (OpenClaw 2026.11.1)
        version = f" {found}" if found else ""
        out.message(f"He encontrado OpenClaw{version} en este equipo.")

    def ask() -> tuple[str, int, str]:
        if answers:
            return answers.openclaw_host, answers.openclaw_port, answers.openclaw_token
        host, port = "127.0.0.1", 18789
        if found is None:
            host = _ask("text", "Host de OpenClaw", default=host, validate=_required).strip()
            port = int(_ask("text", "Puerto de OpenClaw", default=str(port),
                            validate=lambda v: v.strip().isdigit() or "Es un número."))
        out.message(TOKEN_HINT, MUTED)
        token = _ask("password", "Token de OpenClaw", validate=_required).strip()
        return host, port, token

    def check(values: tuple[str, int, str]) -> Any:
        return asyncio.run(verify.verify_openclaw(*values))

    try:
        settled = _settle(out, ask, check, not answers, ("Cancelar", "cancel"))
    except (click.Abort, KeyboardInterrupt):  # Ctrl+C en una pregunta o durante la verificación
        settled = None
    if settled is None:
        raise api.ApiError(NO_OPENCLAW, 1)
    host, port, token = settled
    _write(out, {"OPENCLAW_TOKEN": token, "OPENCLAW_HOST": host, "OPENCLAW_PORT": str(port)})


def _voice_step(out: Out, kind: Literal["stt", "tts"], answer: Any,
                verified: dict[str, str]) -> None:
    """STT o TTS: servicio propio, ElevenLabs u «ahora no» (`off`). Una sola clave de
    ElevenLabs para los dos: la verificada en el STT se reutiliza (y se verifica) en el TTS."""
    from jota_gateway.cli import verify

    name, url_key, token_key, provider_key, default_url = VOICE[kind]

    def ask() -> dict[str, str]:
        provider = answer.provider if answer else _choose(
            f"Voz, {name}: ¿qué servicio?",
            [("Servicio propio", "jota"), ("ElevenLabs", "elevenlabs"), ("Ahora no", "off")])
        if provider == "jota":
            if answer:
                url, token = answer.url or default_url, answer.token
            else:
                url = _ask("text", f"Dirección del servicio de {name} (host:puerto)",
                           default=default_url).strip()
                uses_key = _ask("confirm", "¿Usa clave?", default=False)
                token = _ask("password", f"Clave del servicio de {name}",
                             validate=_required).strip() if uses_key else ""
            if not token:
                out.message(OPEN_SERVICE, MUTED)
            return {url_key: url, token_key: token, provider_key: "jota"}
        if provider == "elevenlabs":
            if ELEVENLABS_KEY in verified:
                out.message("Uso la clave de ElevenLabs que ya has verificado.", MUTED)
                key = verified[ELEVENLABS_KEY]
            else:
                key = answer.api_key if answer else _ask(
                    "password", "Clave de ElevenLabs", validate=_required).strip()
            return {ELEVENLABS_KEY: key, provider_key: "elevenlabs"}
        return {provider_key: "off"}

    def check(values: dict[str, str]) -> Any:
        if values[provider_key] == "off":
            return verify.Verdict(True, "ok", f"{name} desactivado: actívalo cuando quieras "
                                  "con `jota-gw onboard`.")
        if values[provider_key] == "jota":
            jota = getattr(verify, f"verify_jota_{kind}")
            return asyncio.run(jota(values[url_key], values[token_key]))
        return asyncio.run(verify.verify_elevenlabs(kind, values[ELEVENLABS_KEY]))

    values = _settle(out, ask, check, not answer, ("Ahora no", "off")) or {provider_key: "off"}
    _write(out, values)
    verified.update((k, v) for k, v in values.items() if k == ELEVENLABS_KEY)


def _lan_ip() -> str:
    """La IP de la LAN: la de la ruta hacia 8.8.8.8. `connect` en UDP no envía nada."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip: str = s.getsockname()[0]
            return ip
    except OSError:
        return "127.0.0.1"


def _first_client(ctx: click.Context, out: Out, answers: Any) -> None:
    from jota_gateway.cli import clients, menu, serve

    client = api.from_context(ctx)
    if not client.request("GET", "/admin/clients"):
        if answers:
            first = answers.first_client and (answers.first_client.name, answers.first_client.type)
        else:
            first = (_ask("text", "Nombre del primer cliente", validate=_required).strip(),
                     _choose("Tipo", [(t, t) for t in menu.CLIENT_TYPES]))
        if first:
            body = {"name": first[0], "client_type": first[1]}
            clients._show_key(out, client.request("POST", "/admin/clients", body))
    out.message(f"Los clientes se conectan a ws://{_lan_ip()}:{serve.PORT}/ws/stream")


def _read_answers(path: Path) -> Any:
    """El JSON de --answers, validado entero antes de tocar nada. Lleva secretos: tiene que
    ser del usuario y 600 o más estricto, y se comprueba antes de leerlo. Los errores dan la
    clave y el tipo de error, nunca el valor."""
    from typing import Annotated

    from pydantic import BaseModel, ConfigDict, Field, ValidationError

    from jota_gateway.models.admin_schemas import ClientType

    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
                raise api.ApiError(f"{path} lleva secretos: tiene que ser tuyo y solo legible "
                                   f"por ti (`chmod 600 {path}`).", 2)
            raw = f.read()
    except OSError as e:
        raise api.ApiError(f"No se puede leer {path} ({type(e).__name__}).", 2) from None

    class Strict(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

    class Jota(Strict):
        provider: Literal["jota"]
        url: str = ""
        token: str = ""

    class ElevenLabs(Strict):
        provider: Literal["elevenlabs"]
        api_key: str = Field(min_length=1)

    class Off(Strict):
        provider: Literal["off"]

    class FirstClient(Strict):
        name: str = Field(min_length=1)
        type: ClientType

    class Answers(Strict):
        openclaw_host: str
        openclaw_port: int
        openclaw_token: str
        stt: Annotated[Jota | ElevenLabs | Off, Field(discriminator="provider")]
        tts: Annotated[Jota | ElevenLabs | Off, Field(discriminator="provider")]
        first_client: FirstClient | None

    try:
        answers = Answers.model_validate_json(raw)
    except ValidationError as e:
        problems = [": ".join(filter(None, (".".join(str(p) for p in err["loc"]), err["type"])))
                    for err in e.errors()]
        raise api.ApiError(f"{path} no es válido: {'; '.join(problems)}.", 2) from None
    keys = {v.api_key for v in (answers.stt, answers.tts) if isinstance(v, ElevenLabs)}
    if len(keys) > 1:
        raise api.ApiError(f"{path}: stt.api_key y tts.api_key tienen que ser la misma clave "
                           f"(el gateway usa una sola, {ELEVENLABS_KEY}).", 2)
    return answers


def _run(ctx: click.Context, out: Out, answers: Any) -> None:
    from jota_gateway.cli import doctor, service
    from jota_gateway.cli import init as init_mod

    if not paths.config_file().is_file():
        ctx.invoke(init_mod.init)
    cfg._load_or_fail(out)  # antes de preguntar nada: campo y tipo, sin traza
    _openclaw(out, answers)
    verified: dict[str, str] = {}
    _voice_step(out, "stt", answers and answers.stt, verified)
    _voice_step(out, "tts", answers and answers.tts, verified)
    if service.unit_path().exists():
        # Sin la confirmación de `jota-gw restart`: aplicar lo configurado es lo que se ha pedido.
        service._verb(ctx, "restart", "reiniciado", wait=True)
    else:
        ctx.invoke(service.install)
    try:
        ctx.invoke(doctor.doctor)
    except click.exceptions.Exit:
        out.message("El diagnóstico marca algún fallo (arriba); el asistente sigue.", MUTED)
    _first_client(ctx, out, answers)


@click.command()
@click.option("--answers", "answers_path", type=click.Path(dir_okay=False, path_type=Path),
              metavar="FICHERO", help="Respuestas en JSON (permisos 600): no pregunta nada.")
@click.pass_context
def onboard(ctx: click.Context, answers_path: Path | None) -> None:
    """Asistente: OpenClaw, voz, servicio y primer cliente."""
    from jota_gateway.cli import output

    out = Out(ctx.find_root().obj["json"])
    if answers_path is None and not output._stdin_isatty():
        raise api.ApiError("`jota-gw onboard` pregunta y aquí no hay terminal: ejecútalo en "
                           "una, o pásale las respuestas con --answers FICHERO.", 1)
    answers = _read_answers(answers_path) if answers_path else None
    # La CLI no tiene handler de logging: sin uno, logging.lastResort pintaría los WARNING de
    # las librerías entre las preguntas. El usuario ve el Verdict; los niveles no se tocan.
    quiet = logging.NullHandler()
    logging.getLogger().addHandler(quiet)
    try:
        _run(ctx, out, answers)
    finally:
        logging.getLogger().removeHandler(quiet)
