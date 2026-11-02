"""
config.py
~~~~~~~~~
`jota-gw config show | get | validate`: la configuración efectiva, de solo lectura.

Orden, como el gateway: variables del proceso → `<base>/config.env` → valores por defecto
(decisión «Rutas de configuración y datos»). Nunca el `.env` del directorio actual. Los
secretos salen siempre enmascarados, también en --json.
"""

import difflib
import json
import os
import re
import sys
import tempfile
from typing import Any

import click

from src.cli import api, paths
from src.cli.output import Out

MASK_SET = "•••• (definido)"
MASK_EMPTY = "(vacío)"
DEFAULT = "por defecto"
ENVIRON = "entorno"


def _settings_class() -> Any:
    """`Settings`, sin que el import lea el `.env` del directorio actual.

    `src.core.config` construye un `Settings()` global al importarse (env_file=".env"), así que
    se importa desde una carpeta vacía. Una variable de entorno inválida rompe ese global: se
    devuelve como error de validación, igual que lo vería el gateway al arrancar.
    """
    if "src.core.config" in sys.modules:
        return sys.modules["src.core.config"].Settings
    # shortcut: se va con 14c, cuando Settings lea de <base>/config.env.
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as empty:
        os.chdir(empty)
        try:
            from src.core.config import Settings
        finally:
            os.chdir(cwd)
    return Settings


def _errors(exc: Any) -> list[str]:
    # Campo y tipo de error, nunca el valor (podría ser un secreto).
    return [f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}" for e in exc.errors()]


def _settings_error(exc: Exception) -> list[str]:
    # pydantic-settings no da tipo de error al fallar el JSON de un campo (p. ej. un dict).
    match = re.search(r'field "([^"]+)"', str(exc))
    return [f"{match.group(1) if match else 'configuración'}: formato no válido"]


def load() -> tuple[Any, list[str], dict[str, str]]:
    """(settings o None, errores de validación, origen de cada ajuste)."""
    from dotenv import dotenv_values
    from pydantic import ValidationError
    from pydantic_settings import SettingsError

    path = paths.config_file()
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
    from src.core.startup_checks import (
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
            checks.append(_check("warn", name, "Tiene el valor por defecto del repositorio."))
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
    """Configuración efectiva (solo lectura)."""


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
