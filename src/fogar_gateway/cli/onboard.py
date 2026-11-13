"""
onboard.py
~~~~~~~~~~
`fogar-gw onboard`: el asistente de instalación. Pregunta lo mínimo, verifica cada proveedor
contra el servicio real (`cli/verify.py`) antes de guardarlo con `cli/config.py::_apply`,
instala o reinicia el servicio y termina en el dashboard: su URL desde otro aparato, el token
(solo en una terminal), dónde está y cómo verlo, y con terminal espera a que entres. El primer
cliente se empareja desde el dashboard. Con `--answers FICHERO` (JSON 600) no pregunta ni espera
nada, ni enseña el token: es el modo del CI (y su `first_client`, opcional, crea un cliente). Cortarlo a medias deja config.env
como estaba más lo ya verificado; volver a ejecutarlo retoma, y con terminal ofrece primero
«Dejar como está» en cada paso que config.env ya tiene (ni verifica ni escribe).

Con terminal y color (`Out.styled`) se ve la versión de la marca (#328): la entrada con la línea
viva (`cli/linea_viva.py`), un «● Paso» por paso con su resultado en «■ …», `init`, el servicio y
`doctor` resumidos en una línea, y un final con el enlace, su QR y el token copiado al portapapeles
(OSC 52) en vez de impreso. Sin estilo (`--answers`, `--json`, NO_COLOR, sin terminal), la salida
de siempre.

`questionary`, `pydantic`, `rich`, `segno` y los verificadores se importan al usarse (`--help` < 150 ms).
"""

import asyncio
import contextlib
import io
import logging
import os
import stat
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import click

from fogar_gateway import paths
from fogar_gateway.access import _is_loopback, collect, configured_public_urls
from fogar_gateway.cli import api
from fogar_gateway.cli import config as cfg
from fogar_gateway.cli.access import gateway_instance_id, show_routes
from fogar_gateway.cli.output import ACCENT, MUTED, Out

NO_OPENCLAW = "Sin OpenClaw no se instala el servicio. Vuelve a ejecutar `fogar-gw onboard`."
TOKEN_HINT = "Ejecuta `openclaw config get gateway.auth.token` y pega el resultado."
OPEN_SERVICE = "Recomendado: deja este servicio accesible solo desde el gateway."
ELEVENLABS_KEY = "ELEVENLABS_API_KEY"
# tipo -> (nombre, ajuste de la dirección, de la clave, del proveedor, dirección por defecto)
VOICE = {
    "stt": ("STT", "TRANSCRIBER_WS_URL", "TRANSCRIBER_TOKEN", "STT_PROVIDER", "localhost:9000"),
    "tts": ("TTS", "TTS_WS_URL", "TTS_TOKEN", "TTS_PROVIDER", "localhost:8005"),
}
DASHBOARD_POLL_S = 1.0
KEEP = {"fogar": "servicio propio", "elevenlabs": "ElevenLabs", "off": "desactivado"}


def _ask(kind: str, message: str, **kwargs: Any) -> Any:
    """Una pregunta de questionary; Ctrl+C (questionary devuelve None) cancela el asistente."""
    import questionary

    from fogar_gateway.cli.menu import SELECT_HINT, _style

    if kind == "confirm":  # questionary solo entiende y/n: se dice en castellano qué tecla es qué
        yes = kwargs.get("default", True)
        kwargs.setdefault("instruction", "(Enter: sí · n: no) " if yes else "(Enter: no · y: sí) ")
    elif kind == "select":
        kwargs.setdefault("instruction", SELECT_HINT)
    answer = getattr(questionary, kind)(message, style=_style(), qmark="\u203a", **kwargs).ask()
    if answer is None:
        raise click.Abort
    return answer


def _choose(message: str, options: list[tuple[str, str]], default: str | None = None) -> str:
    import questionary

    choices = [questionary.Choice(title, value=value) for title, value in options]
    answer: str = _ask("select", message, choices=choices, default=default)
    return answer


def _file() -> dict[str, str | None]:
    """config.env tal cual (sin entorno ni valores por defecto): lo que ya está configurado."""
    from dotenv import dotenv_values

    return dotenv_values(paths.config_file())


def _required(value: str) -> bool | str:
    return bool(value.strip()) or "Es obligatorio."


@contextlib.contextmanager
def _busy(out: Out, text: str) -> Iterator[None]:
    """Con estilo, un spinner en brasa mientras dura (no deja rastro); sin estilo, nada."""
    if not out.styled:
        yield
        return
    import sys

    from fogar_gateway.cli.output import BRASA

    console = out._console()
    console.file = sys.stdout  # la terminal de ahora: un `_hidden` posterior no se lo traga
    with console.status(text, spinner="dots", spinner_style=BRASA):
        yield


@contextlib.contextmanager
def _hidden(out: Out) -> Iterator[None]:
    """Con estilo, calla la salida de un subcomando (init, install): su resumen lo pinta el paso.
    Los errores siguen saliendo: van a stderr o como excepción."""
    if not out.styled:
        yield
        return
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def _settle[T](out: Out, ask: Callable[[], T], check: Callable[[T], Any], interactive: bool,
               give_up: tuple[str, str]) -> T | None:
    """Pregunta y verifica hasta que pase. Sin terminal, un fallo sale con 1 y su detalle;
    con terminal, se elige reintentar, cambiar las respuestas o `give_up` (devuelve None)."""
    values = ask()
    while True:
        with _busy(out, "Comprobando…"):
            verdict = check(values)
        if verdict.ok:
            out.ok(verdict.detail)
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


def _openclaw(out: Out, answers: Any, settings: Any) -> None:
    from fogar_gateway.cli import verify

    if not answers and _file().get("OPENCLAW_TOKEN"):
        where = f"{settings.OPENCLAW_HOST}:{settings.OPENCLAW_PORT}"
        if _choose("OpenClaw ya está configurado: ¿lo cambio?",
                   [(f"Dejar como está ({where})", "keep"), ("Cambiarlo", "change")],
                   default="keep") == "keep":
            out.ok(f"OpenClaw: sin cambios ({where}).")
            return
    found = None if answers else asyncio.run(verify.detect_openclaw())
    if found is not None:  # "" = encontrado, sin versión (OpenClaw 2026.11.1)
        version = f" {found}" if found else ""
        out.note(f"He encontrado OpenClaw{version} en este equipo.")

    def ask() -> tuple[str, int, str]:
        if answers:
            return answers.openclaw_host, answers.openclaw_port, answers.openclaw_token
        host, port = "127.0.0.1", 18789
        if found is None:
            host = _ask("text", "Host de OpenClaw", default=host, validate=_required).strip()
            port = int(_ask("text", "Puerto de OpenClaw", default=str(port),
                            validate=lambda v: v.strip().isdigit() or "Es un número."))
        out.note(TOKEN_HINT)
        token = _ask("password", "Token de OpenClaw", validate=_required).strip()
        return host, port, token

    def check(values: tuple[str, int, str]) -> Any:
        return asyncio.run(verify.verify_openclaw(*values))

    try:
        settled = _settle(out, ask, check, not answers, ("Cancelar", "cancel"))
    except (click.Abort, KeyboardInterrupt):  # Ctrl+C en una pregunta o durante la verificación
        settled = None
    except api.ApiError as e:  # --answers: el detalle del fallo y después el mensaje fijo
        out.error(e.message)
        settled = None
    if settled is None:
        raise api.ApiError(NO_OPENCLAW, 1)
    host, port, token = settled
    _write(out, {"OPENCLAW_TOKEN": token, "OPENCLAW_HOST": host, "OPENCLAW_PORT": str(port)})


def _on_other_key(kind: str, key: str) -> bool:
    """¿El `kind` de config.env ya usa ElevenLabs con otra clave? Al cambiarla, la usaría sin
    haberla verificado para él (el gateway tiene una sola ELEVENLABS_API_KEY)."""
    current = _file()
    return current.get(VOICE[kind][3]) == "elevenlabs" and current.get(ELEVENLABS_KEY) != key


def _voice_step(out: Out, kind: Literal["stt", "tts"], answer: Any,
                verified: dict[str, str], other_stays: bool = True) -> None:
    """STT o TTS: servicio propio, ElevenLabs u «ahora no» (`off`), o con terminal «Dejar como
    está» si config.env ya lo tiene. Una sola clave de ElevenLabs para los dos: la verificada en
    el STT se reutiliza (y se verifica) en el TTS. `other_stays`: el otro servicio podría seguir
    en ElevenLabs tras esta ejecución (el STT, ya resuelto, siempre; el TTS, salvo --answers con
    TTS `off`); si ya lo usa, una clave nueva se verifica también para él antes de escribirla."""
    from fogar_gateway.cli import verify

    name, url_key, token_key, provider_key, default_url = VOICE[kind]
    other: Literal["stt", "tts"] = "tts" if kind == "stt" else "stt"
    current = None if answer else _file().get(provider_key)

    def ask() -> dict[str, str]:
        options = [("Servicio propio", "fogar"), ("ElevenLabs", "elevenlabs"), ("Ahora no", "off")]
        if current:
            options.insert(0, (f"Dejar como está ({KEEP.get(current, current)})", "keep"))
        provider = answer.provider if answer else _choose(
            f"Voz, {name}: ¿qué servicio?", options, default="keep" if current else None)
        if provider == "keep":
            return {}
        if provider == "fogar":
            if answer:
                url, token = answer.url or default_url, answer.token
            else:
                url = _ask("text", f"Dirección del servicio de {name} (host:puerto)",
                           default=default_url).strip()
                uses_key = _ask("confirm", "¿Usa clave?", default=False)
                token = _ask("password", f"Clave del servicio de {name}",
                             validate=_required).strip() if uses_key else ""
            if not token:
                out.note(OPEN_SERVICE)
            return {url_key: url, token_key: token, provider_key: "fogar"}
        if provider == "elevenlabs":
            if ELEVENLABS_KEY in verified:
                out.note("Uso la clave de ElevenLabs que ya has verificado.")
                key = verified[ELEVENLABS_KEY]
            else:
                key = answer.api_key if answer else _ask(
                    "password", "Clave de ElevenLabs", validate=_required).strip()
            return {ELEVENLABS_KEY: key, provider_key: "elevenlabs"}
        return {provider_key: "off"}

    def check(values: dict[str, str]) -> Any:
        if not values:
            return verify.Verdict(True, "ok", f"{name}: sin cambios.")
        if values[provider_key] == "off":
            return verify.Verdict(True, "ok", f"{name} desactivado: actívalo cuando quieras "
                                  "con `fogar-gw onboard`.")
        if values[provider_key] == "fogar":
            fogar = getattr(verify, f"verify_fogar_{kind}")
            return asyncio.run(fogar(values[url_key], values[token_key]))
        key = values[ELEVENLABS_KEY]
        verdict = asyncio.run(verify.verify_elevenlabs(kind, key))
        if verdict.ok and other_stays and _on_other_key(other, key):
            also = asyncio.run(verify.verify_elevenlabs(other, key))
            if not also.ok:
                o = VOICE[other][0]
                return verify.Verdict(False, also.reason, f"El {o} ya usa ElevenLabs y pasaría a "
                                      f"esta clave, pero para el {o} no vale: {also.detail}")
        return verdict

    values = _settle(out, ask, check, not answer, ("Ahora no", "off"))
    if values is None:  # «Ahora no» tras un fallo ({} es «Dejar como está»: no escribe nada)
        values = {provider_key: "off"}
    _write(out, values)
    verified.update((k, v) for k, v in values.items() if k == ELEVENLABS_KEY)


def _first_client(ctx: click.Context, out: Out, answers: Any) -> None:
    """Solo con --answers: el CI crea su cliente. Con terminal, se empareja desde el dashboard."""
    from fogar_gateway.cli import clients

    if not (answers and answers.first_client):
        return
    client = api.from_context(ctx)
    if not client.request("GET", "/admin/clients"):
        body = {"name": answers.first_client.name, "client_type": answers.first_client.type}
        clients._show_key(out, client.request("POST", "/admin/clients", body))


def _enter_pressed(timeout: float) -> bool:
    """¿Ha pulsado Enter en `timeout` s? Lee la línea para que no quede en la terminal."""
    import select
    import sys

    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if ready:
        sys.stdin.readline()
    return bool(ready)


@contextlib.contextmanager
def _no_echo(out: Out) -> Iterator[None]:
    """Con estilo, la terminal no repite el Enter: su salto de línea descolocaría el spinner."""
    import sys

    if not out.styled or not sys.stdin.isatty():
        yield
        return
    import termios

    fd = sys.stdin.fileno()
    before = termios.tcgetattr(fd)
    quiet = termios.tcgetattr(fd)
    quiet[3] &= ~termios.ECHO
    termios.tcsetattr(fd, termios.TCSANOW, quiet)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, before)


def _wait_dashboard(ctx: click.Context, out: Out, url: str | None) -> None:
    """Espera a que la pantalla Estado del dashboard abra su flujo (`gateway.dashboards`).
    Enter o Ctrl+C dejan de esperar sin error; un gateway que aún arranca, se reintenta."""
    client = api.from_context(ctx)
    if out.styled:
        waiting = "Te espero en el dashboard… (Enter para seguir sin esperar)"
    else:
        out.message("Abre el dashboard en otro aparato y pega el token. Te espero… "
                    "(Enter para seguir sin esperar)", MUTED)
        waiting = ""
    with _no_echo(out), _busy(out, waiting):
        connected = _poll_dashboard(client)
    if connected:
        out.ok("Conectado: ya puedes seguir en el dashboard.")
        return
    out.note(f"Sin esperar: entra cuando quieras{f' en {url}' if url else ''}.")


def _poll_dashboard(client: Any) -> bool:
    """True cuando un dashboard abre su flujo; False con Enter o Ctrl+C."""
    try:
        while True:
            try:
                state = client.request("GET", "/admin/state")
            except api.ApiError:
                state = {}
            if state.get("gateway", {}).get("dashboards"):
                return True
            if _enter_pressed(DASHBOARD_POLL_S):
                return False
    except KeyboardInterrupt:
        return False


def _qr(url: str) -> Any:
    """El QR de `url` en medios bloques, negro sobre blanco forzado: se lee igual en una terminal
    clara que en una oscura."""
    import segno
    from rich.text import Text

    rows = [list(r) for r in segno.make(url, error="l").matrix_iter(scale=1, border=2)]
    if len(rows) % 2:
        rows.append([0] * len(rows[0]))
    out = Text(no_wrap=True)
    for top, bottom in zip(rows[::2], rows[1::2], strict=True):
        out.append("  ")
        for a, b in zip(top, bottom, strict=True):
            out.append("▀", style=f"{'#000000' if a else '#ffffff'} on {'#000000' if b else '#ffffff'}")
        out.append("\n")
    return out


def _copy_to_clipboard(value: str) -> None:
    """OSC 52: la terminal del usuario lo copia en su portapapeles, también por ssh. No hay
    respuesta: no se sabe si la terminal lo admite (el Terminal de macOS, no)."""
    import base64
    import sys

    sys.stdout.write(f"\x1b]52;c;{base64.b64encode(value.encode()).decode()}\x07")
    sys.stdout.flush()


def _dashboard_styled(out: Out, routes: list[Any], settings: Any,
                      lan: Any) -> str | None:
    """El final con estilo: el enlace del dashboard, su QR y el token al portapapeles; el token
    nunca en pantalla. Devuelve la URL recomendada."""
    from rich.text import Text

    from fogar_gateway.cli.access import HTTPS_HINT, _status_text
    from fogar_gateway.cli.token import WARNING

    out.step("Tu dashboard")
    rec = next((r for r in routes if r.recommended), None)
    console = out._console()
    if rec:
        console.print(Text.assemble("  ", (rec.url, f"bold link {rec.url}"),
                                    (f"  {_status_text(rec)}", MUTED)))
        if console.width >= 40:
            console.print(_qr(rec.url))
    for r in routes:
        if r is not rec:
            out.note(f"{r.url}  ({_status_text(r)})")
    if rec and not rec.https:
        out.note(HTTPS_HINT)
    _copy_to_clipboard(settings.ADMIN_TOKEN)
    out.message("  Para entrar, pega el token de administración. Te lo he mandado al portapapeles "
                "(no se imprime).")
    out.note(f"Si no está en tu portapapeles (el Terminal de macOS no lo permite), míralo con "
             f"`fogar-gw token` (está en {paths.config_file()}).")
    out.note(WARNING)
    out.ok("Empareja tu primer aparato desde Clientes, en el dashboard.")
    if lan:
        out.note(f"Los clientes se conectan a ws://{urlsplit(lan.url).netloc}/ws/stream")
    return rec.url if rec else None


def _dashboard(ctx: click.Context, out: Out, answers: Any, settings: Any) -> None:
    """El final: cómo llegar al dashboard desde otro aparato y con qué token."""
    from fogar_gateway.cli import output, serve
    from fogar_gateway.cli.token import WARNING

    port = serve.PORT
    bind = settings.GATEWAY_HOST
    routes = collect(bind, port, configured_public_urls(),
                     gateway_instance_id(ctx.find_root().obj["url"]))
    rec = next((r for r in routes if r.recommended), None)
    url = rec.url if rec else None
    lan = next((r for r in routes if r.kind == "lan"), None)
    if routes and out.styled:
        _wait_dashboard(ctx, out, _dashboard_styled(out, routes, settings, lan))
        return
    if routes:
        show_routes(out, routes, "Tu dashboard")
    elif _is_loopback(bind):
        out.message(f"El gateway solo escucha en este equipo (GATEWAY_HOST={bind}): desde otro "
                    f"aparato no se llega. Entra con un túnel SSH (`ssh -L {port}:127.0.0.1:{port}"
                    f" <este servidor>` y abre http://localhost:{port}/) o ábrelo a la LAN con "
                    "`fogar-gw config set GATEWAY_HOST 0.0.0.0` y `fogar-gw restart`.")
    else:
        out.message(f"Tu dashboard: http://<IP de este equipo>:{port}/ (no encuentro la IP "
                    "de la LAN: mírala con `hostname -I`)")
    out.message("Para entrar, pega en él el token de administración.")
    if answers is None and not out.as_json and output._isatty():
        out.message(f"Token: {settings.ADMIN_TOKEN}", ACCENT)
    out.message(f"Está en {paths.config_file()} (ADMIN_TOKEN). Para verlo otra vez: "
                "`fogar-gw token`.")
    out.message(WARNING, MUTED)
    out.message("Empareja tu primer aparato desde Clientes, en el dashboard.")
    if lan:
        out.message(f"Los clientes se conectan a ws://{urlsplit(lan.url).netloc}"
                    "/ws/stream", MUTED)
    if answers is None:
        _wait_dashboard(ctx, out, url)


def _read_answers(path: Path) -> Any:
    """El JSON de --answers, validado entero antes de tocar nada. Lleva secretos: tiene que
    ser del usuario y 600 o más estricto, y se comprueba antes de leerlo. Los errores dan la
    clave y el tipo de error, nunca el valor."""
    from typing import Annotated

    from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

    from fogar_gateway.models.admin_schemas import ClientType, _Name

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

    class Fogar(Strict):
        provider: Literal["fogar"]
        url: str = ""
        token: str = ""

    class ElevenLabs(Strict):
        provider: Literal["elevenlabs"]
        api_key: str = Field(min_length=1)

    class Off(Strict):
        provider: Literal["off"]

    class FirstClient(Strict):  # las reglas del servidor: un nombre malo falla antes de instalar
        name: _Name
        type: ClientType

    class Access(Strict):
        tailscale_serve: bool = False
        public_urls: list[str] = Field(default_factory=list)

        @field_validator("public_urls")
        @classmethod
        def _urls(cls, v: list[str]) -> list[str]:
            from fogar_gateway.access import parse_public_urls

            for url in v:  # ValueError -> value_error, sin el valor
                if not parse_public_urls(url):  # "" o espacios: no es una URL
                    raise ValueError("vacía")
            return v

    class Answers(Strict):
        openclaw_host: str
        openclaw_port: int
        openclaw_token: str
        stt: Annotated[Fogar | ElevenLabs | Off, Field(discriminator="provider")]
        tts: Annotated[Fogar | ElevenLabs | Off, Field(discriminator="provider")]
        first_client: FirstClient | None = None  # el CI; con terminal, desde el dashboard
        access: Access | None = None  # sin él, el paso no se ejecuta (el CI de siempre)

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


def _doctor_summary(ctx: click.Context, out: Out) -> None:
    """Con estilo, `doctor` en una línea si todo va bien; si no, solo lo que avisa o falla."""
    from fogar_gateway.cli import doctor

    obj = ctx.find_root().obj
    with _busy(out, "Diagnosticando…"):
        results = doctor.run_checks(obj["url"], obj["token"])
    bad = [c for c in results if c["level"] in ("warn", "fail")]
    if not bad:
        good = sum(c["level"] == "ok" for c in results)
        out.ok(f"Diagnóstico: {good} comprobaciones correctas.")
        return
    for c in bad:
        text = f"{c['check']}: {c['detail']}"
        if c["level"] == "warn":
            out.warn(text)
        else:
            out.error(text)
    out.note("El asistente sigue. El detalle completo, con `fogar-gw doctor`.")


def _run(ctx: click.Context, out: Out, answers: Any) -> None:
    from fogar_gateway.cli import doctor, service
    from fogar_gateway.cli import init as init_mod

    service.require_systemd()  # antes de crear o preguntar nada
    styled = out.styled  # --answers lo apaga en `onboard`
    if styled:
        from fogar_gateway.cli import linea_viva

        linea_viva.play(out._console())
        out.note("Gateway · puesta en marcha")
    settings, errors, _ = cfg.load()
    db = paths.sqlite_file(settings.DATABASE_URL) if settings else None
    # init no toca lo que ya existe: también retoma una base con config.env y sin BD.
    if not paths.config_file().is_file() or (db is not None and not db.exists()):
        with _hidden(out):
            ctx.invoke(init_mod.init)
        if styled:
            out.note(f"Base nueva en {paths.home()}: configuración y base de datos creadas.")
    settings, _ = cfg._load_or_fail(out)  # antes de preguntar nada: campo y tipo, sin traza
    before = paths.config_file().read_bytes()  # para no reiniciar si el relanzamiento no cambia nada
    out.step("OpenClaw")
    _openclaw(out, answers, settings)
    out.step("Voz")
    verified: dict[str, str] = {}
    # Solo un TTS `off` en el fichero se libra: su paso no falla. Con fogar o ElevenLabs, el paso
    # TTS puede fallar (o cortarse) y dejar el TTS en ElevenLabs con la clave nueva.
    tts_stays = answers is None or answers.tts.provider != "off"
    _voice_step(out, "stt", answers and answers.stt, verified, tts_stays)
    _voice_step(out, "tts", answers and answers.tts, verified)
    if answers is None or answers.access is not None:
        from fogar_gateway.cli import access as cli_access

        out.step("Acceso")
        cli_access.access_step(out, answers and answers.access, settings)
    out.step("Servicio")
    if not service.unit_path().exists():
        with _busy(out, "Instalando y arrancando el servicio…"), _hidden(out):
            ctx.invoke(service.install)
        if styled:
            out.ok("Servicio instalado y en marcha (se arranca solo con el equipo).")
    elif paths.config_file().read_bytes() == before and service.is_active():
        # Relanzar sin cambiar nada no corta las sesiones abiertas.
        out.ok("Sin cambios en la configuración: el servicio sigue en marcha, sin reiniciar.")
    # Nunca el confirm() de `fogar-gw restart`: sin terminal saldría con 2 y config.env escrito.
    elif answers or _ask("confirm", "Reinicio el servicio para aplicarlo; se cortarán las "
                         "sesiones abiertas. ¿Sigo?", default=True):
        service._verb(ctx, "restart", "reiniciado", wait=True)
    else:
        out.message("Sin reiniciar: lo nuevo se aplica con `fogar-gw restart`.", MUTED)
    if styled:
        _doctor_summary(ctx, out)
    else:
        try:
            ctx.invoke(doctor.doctor)
        except click.exceptions.Exit:
            out.message("El diagnóstico marca algún fallo (arriba); el asistente sigue.", MUTED)
    _first_client(ctx, out, answers)
    _dashboard(ctx, out, answers, settings)


@click.command()
@click.option("--answers", "answers_path", type=click.Path(dir_okay=False, path_type=Path),
              metavar="FICHERO", help="Respuestas en JSON (permisos 600): no pregunta nada.")
@click.pass_context
def onboard(ctx: click.Context, answers_path: Path | None) -> None:
    """Asistente: OpenClaw, voz, servicio y dashboard."""
    from fogar_gateway.cli import output

    out = Out(ctx.find_root().obj["json"])
    if answers_path is None and not output._stdin_isatty():
        raise api.ApiError("`fogar-gw onboard` pregunta y aquí no hay terminal: ejecútalo en "
                           "una, o pásale las respuestas con --answers FICHERO.", 1)
    answers = _read_answers(answers_path) if answers_path else None
    if answers is not None:  # el modo del CI: la salida de siempre, sin color ni animación
        out.styled = False
    # La CLI no tiene handler de logging: sin uno, logging.lastResort pintaría los WARNING de
    # las librerías entre las preguntas. El usuario ve el Verdict; los niveles no se tocan.
    quiet = logging.NullHandler()
    logging.getLogger().addHandler(quiet)
    try:
        _run(ctx, out, answers)
    finally:
        logging.getLogger().removeHandler(quiet)
