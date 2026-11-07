"""
config.py
~~~~~~~~~
`jota-gw config show | get | validate | set | unset`: la configuración efectiva y su edición.

Orden, como el gateway: variables del proceso → `<base>/config.env` → valores por defecto
(decisión «Rutas de configuración y datos»). Nunca el `.env` del directorio actual. Los
secretos salen siempre enmascarados, también en --json.
"""

import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import click

from jota_gateway import paths
from jota_gateway.cli import api
from jota_gateway.cli.output import Out

MASK_SET = "•••• (definido)"
MASK_EMPTY = "(vacío)"
DEFAULT = "por defecto"
ENVIRON = "entorno"


def _settings_class() -> Any:
    """`Settings`, importado solo al usarse (`--help` no carga pydantic).

    `jota_gateway.core.config` construye un `Settings()` global al importarse: una variable de
    entorno inválida lo rompe y sale como error de validación, igual que al arrancar el gateway.
    """
    from jota_gateway.core.config import Settings

    return Settings


def _errors(exc: Any) -> list[str]:
    # Campo y tipo de error, nunca el valor (podría ser un secreto).
    return [f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}" for e in exc.errors()]


def _settings_error(exc: Exception) -> list[str]:
    # pydantic-settings no da tipo de error al fallar el JSON de un campo (p. ej. un dict).
    match = re.search(r'field "([^"]+)"', str(exc))
    return [f"{match.group(1) if match else 'configuración'}: formato no válido"]


def load(path: Path | None = None) -> tuple[Any, list[str], dict[str, str]]:
    """(settings o None, errores de validación, origen de cada ajuste) de `path` (o config.env)."""
    from dotenv import dotenv_values
    from pydantic import ValidationError
    from pydantic_settings import SettingsError

    path = path or paths.config_file()
    from_file: dict[str, Any] = {}
    if path.is_file():
        try:
            api.read_env_file(path)  # mensaje legible si no hay permiso
        except api.ApiError as e:
            return None, [e.message], {}
        from_file = {k.upper(): v for k, v in dotenv_values(path).items()}
    try:
        settings_cls = _settings_class()
        settings = settings_cls(_env_file=path if path.is_file() else None)
    except ValidationError as e:
        return None, _errors(e), {}
    except SettingsError as e:
        return None, _settings_error(e), {}
    environ = {k.upper() for k in os.environ}
    origins = {
        name: ENVIRON if name in environ else str(path) if name in from_file else DEFAULT
        for name in type(settings).model_fields
    }
    return settings, [], origins


def is_secret(name: str, value: Any) -> bool:
    from pydantic import SecretStr

    return name.endswith(("_TOKEN", "_KEY")) or isinstance(value, SecretStr)


def shown(name: str, value: Any) -> Any:
    if is_secret(name, value):
        raw = value.get_secret_value() if hasattr(value, "get_secret_value") else value
        return MASK_SET if raw else MASK_EMPTY
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and "://" in value and "@" in value:
        return _mask_url_password(value)
    return value


def _mask_url_password(url: str) -> str:
    """`postgresql://u:pw@h/db` → `postgresql://u:••••@h/db` (p. ej. DATABASE_URL)."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if not parts.password:
        return url
    netloc = parts.netloc.replace(f":{parts.password}@", ":••••@", 1)
    return parts._replace(netloc=netloc).geturl()


def validation_checks(settings: Any) -> list[dict[str, str]]:
    """Comprobaciones de la configuración, compartidas por `config validate` y `doctor`."""
    from jota_gateway.core.startup_checks import (
        SpeechConfigError,
        check_speech_settings,
        deprecated_settings,
        insecure_tokens,
    )

    where = paths.config_file()
    checks: list[dict[str, str]] = []
    for name in insecure_tokens(settings):
        empty = not getattr(settings, name)
        if name == "ADMIN_TOKEN" and empty:
            checks.append(_check("fail", name, f"Vacío: /admin no funciona. Defínelo en {where}."))
        elif name == "TTS_TOKEN":
            checks.append(_check("warn", name, "Es un valor de ejemplo."))
        else:
            checks.append(_check("warn", name, "Vacío o con un valor de ejemplo."))
    try:
        check_speech_settings(
            settings, stt_provider=settings.STT_PROVIDER, tts_provider=settings.TTS_PROVIDER
        )
    except SpeechConfigError as e:
        checks.append(_check("fail", e.setting, "Inválido o ausente para el proveedor elegido."))
    for name in deprecated_settings(settings):
        checks.append(_check("warn", name, "Obsoleto e ignorado; bórralo de la configuración."))
    return checks


def _check(level: str, name: str, detail: str) -> dict[str, str]:
    return {"level": level, "check": name, "detail": detail}


def _load_or_fail(out: Out) -> tuple[Any, dict[str, str]]:
    settings, errors, origins = load()
    if errors:
        if out.as_json:
            out.json({"errors": errors})
        else:
            out.error("La configuración no es válida:")
            for err in errors:
                out.error(f"  {err}")
        raise click.exceptions.Exit(1)
    return settings, origins


def _out(ctx: click.Context) -> Out:
    return Out(ctx.find_root().obj["json"])


@click.group()
def config() -> None:
    """Configuración: ver, validar y cambiar."""


@config.command()
@click.pass_context
def show(ctx: click.Context) -> None:
    """Todos los ajustes, con su valor y de dónde sale."""
    out = _out(ctx)
    settings, origins = _load_or_fail(out)
    rows = {n: {"value": shown(n, getattr(settings, n)), "origin": o} for n, o in origins.items()}
    if out.as_json:
        out.json(rows)
    else:
        out.table(
            [("AJUSTE", "name"), ("VALOR", "value"), ("ORIGEN", "origin")],
            [{"name": n, **r} for n, r in rows.items()],
        )


@config.command()
@click.argument("name")
@click.pass_context
def get(ctx: click.Context, name: str) -> None:
    """Un ajuste (sin distinguir mayúsculas)."""
    out = _out(ctx)
    settings, origins = _load_or_fail(out)
    key = name.upper()
    if key not in origins:
        close = difflib.get_close_matches(key, origins, n=1)
        hint = f" ¿Querías decir {close[0]}?" if close else ""
        raise api.ApiError(f"No existe el ajuste «{name}».{hint}", 2)
    value = shown(key, getattr(settings, key))
    if out.as_json:
        out.json({"name": key, "value": value, "origin": origins[key]})
    else:
        out.pairs([(key, value), ("origen", origins[key])])


@config.command()
@click.pass_context
def validate(ctx: click.Context) -> None:
    """Comprueba la configuración; sale con 1 si hay algún fallo."""
    out = _out(ctx)
    settings, _ = _load_or_fail(out)
    checks = validation_checks(settings)
    if out.as_json:
        out.json(checks)
    elif not checks:
        out.message("La configuración es válida.")
    else:
        for c in checks:
            mark = "✗" if c["level"] == "fail" else "!"
            out.message(f"{mark} {c['check']}: {c['detail']}")
    if any(c["level"] == "fail" for c in checks):
        raise click.exceptions.Exit(1)


# --- set / unset ---------------------------------------------------------------------------

_BARE = re.compile(r"[A-Za-z0-9_./:@%+,-]+")


def _quote(value: str) -> str:
    # Comillas simples: dotenv no interpola `$VAR` en ellas (en las dobles, `${VAR}` sí). Solo
    # `\` y `'` se escapan. shortcut: `${X}` no es representable ni así; `_apply` lo rechaza.
    if _BARE.fullmatch(value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _line_re(key: str) -> "re.Pattern[str]":
    return re.compile(rf"\s*(export\s+)?{re.escape(key)}\s*=", re.IGNORECASE)


def _edit(text: str, key: str, value: str | None) -> str:
    """`set` (value) o `unset` (None) conservando comentarios y orden; duplicados fuera."""
    pat = _line_re(key)
    out: list[str] = []
    done = value is None
    for line in text.splitlines(keepends=True):
        if not pat.match(line):
            out.append(line)
        elif not done:
            out.append(f"{key}={_quote(value or '')}\n")
            done = True
    if not done:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{key}={_quote(value or '')}\n")
    return "".join(out)


def _fails(path: Path, key: str) -> tuple[set[str], list[str]]:
    # Sin la variable de NAME en el entorno: el servicio de systemd no hereda esta shell, así
    # que lo que vale es lo que dice el fichero.
    names = [n for n in os.environ if n.upper() == key]
    saved = {n: os.environ.pop(n) for n in names}
    try:
        settings, errors, _ = load(path)
    finally:
        os.environ.update(saved)
    if errors:
        return set(), errors
    return {c["check"] for c in validation_checks(settings) if c["level"] == "fail"}, []


def _resolve_name(name: str) -> str:
    fields = _settings_class().model_fields
    key = name.upper()
    if key not in fields:
        close = difflib.get_close_matches(key, list(fields), n=1)
        hint = f" ¿Querías decir {close[0]}?" if close else ""
        raise api.ApiError(f"No existe el ajuste «{name}».{hint}", 2)
    return key


def _apply(out: Out, key: str, value: str | None) -> bool:
    """Valida el candidato en un temporal 600 y lo mueve sobre config.env. False = sin cambios."""
    from dotenv import dotenv_values

    path = paths.config_file()
    if not path.is_file():
        raise api.ApiError(f"No hay {path}. Prepara la instalación con `jota-gw init`.", 1)
    old = path.read_text()
    new = _edit(old, key, value)
    if new == old:
        return False
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new)
        if value is not None and dotenv_values(tmp).get(key) != value:
            raise api.ApiError(f"El valor de {key} no se puede guardar en config.env tal cual "
                               "(p. ej. contiene `${...}` o saltos de línea).", 1)
        before, _ = _fails(path, key)
        after, errors = _fails(tmp, key)
        if errors:
            raise api.ApiError("Con ese cambio la configuración no sería válida: "
                               + "; ".join(errors) + ". No se ha escrito nada.", 1)
        if after - before:
            raise api.ApiError("Con ese cambio fallaría: " + ", ".join(sorted(after - before))
                               + ". No se ha escrito nada.", 1)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def _offer_restart(ctx: click.Context, out: Out) -> bool:
    """Reinicia si la unidad existe y está activa y se acepta; True si reinició. Nunca aborta."""
    from jota_gateway.cli import output, service

    try:
        service.require_systemd()
    except api.ApiError:
        return False
    if not service.unit_path().exists() or service.systemctl(
            "is-active", "--quiet", service.UNIT_NAME, check=False).returncode != 0:
        return False
    # No `output.confirm`: sale con 2 al decir que no y aquí el cambio ya está hecho.
    # --json no es interactivo: solo reinicia con --yes.
    yes = bool(ctx.find_root().obj["yes"])
    if yes or (not out.as_json and output._stdin_isatty() and click.confirm(
            "¿Reiniciar el servicio para aplicarlo?", err=True)):
        _restart(ctx)
        return True
    return False


def _restart(ctx: click.Context) -> None:
    """Como `jota-gw restart`; config.env ya está escrito, así que un fallo lo dice."""
    from jota_gateway.cli import service

    try:
        service.systemctl_or_fail("restart", service.UNIT_NAME)
        service.wait_healthy(ctx.find_root().obj["url"])
    except api.ApiError as e:
        hint = "" if "logs -n 50" in e.message else " Mira los registros con `jota-gw logs -n 50`."
        raise api.ApiError("El cambio está guardado en config.env, pero el servicio no ha "
                           f"arrancado: {e.message}{hint}", 1) from None


def _report(ctx: click.Context, out: Out, key: str, value: str | None, changed: bool) -> None:
    # Siempre el valor guardado (nunca el del entorno), enmascarado si es un secreto.
    saved = None if value is None else shown(key, value)
    restarted = changed and _offer_restart(ctx, out)
    if out.as_json:
        out.json({"name": key, "value": saved, "changed": changed, "restarted": restarted})
        return
    if not changed:
        out.message(f"{key}: sin cambios.")
    elif saved is None:
        out.message(f"{key} quitado de config.env (vuelve al valor por defecto).")
    else:
        out.message(f"{key}={saved}")
    if key in {k.upper() for k in os.environ}:
        out.message(f"Aviso: {key} está en el entorno y en esta shell manda sobre config.env.")
    if restarted:
        out.message("Servicio reiniciado.")
    elif changed:
        out.message("Para aplicarlo: jota-gw restart")


@config.command(name="set")
@click.argument("name")
@click.argument("value", required=False)
@click.pass_context
def set_(ctx: click.Context, name: str, value: str | None) -> None:
    """Fija un ajuste en config.env. VALUE `-` lo lee de stdin; los secretos solo así."""
    out = _out(ctx)
    key = _resolve_name(name)
    field = _settings_class().model_fields[key]
    secret = is_secret(key, field.default)
    if secret and value not in (None, "-"):
        raise api.ApiError(f"{key} es un secreto: no se pasa como argumento (queda en el "
                           "historial). Usa `-` y escríbelo por stdin, o no pongas valor.", 2)
    if value is None:
        if not secret:
            raise api.ApiError(f"Falta el valor de {key}.", 2)
        from jota_gateway.cli import output

        if not output._stdin_isatty():
            raise api.ApiError(f"Sin terminal para pedir {key}: pásalo por stdin con `-`.", 2)
        value = click.prompt(key, hide_input=True, err=True)
    elif value == "-":
        from jota_gateway.cli import output

        # Con terminal, el prompt oculto: leer stdin mostraría el secreto al teclearlo.
        value = (click.prompt(key, hide_input=True, err=True) if output._stdin_isatty()
                 else sys.stdin.read().rstrip("\r\n"))
    if "\n" in value or "\r" in value:
        raise api.ApiError(f"El valor de {key} no puede tener saltos de línea.", 2)
    changed = _apply(out, key, value)
    _report(ctx, out, key, value, changed)


@config.command()
@click.argument("name")
@click.pass_context
def unset(ctx: click.Context, name: str) -> None:
    """Quita un ajuste de config.env (vuelve a su valor por defecto)."""
    out = _out(ctx)
    key = _resolve_name(name)
    changed = _apply(out, key, None)
    _report(ctx, out, key, None, changed)
