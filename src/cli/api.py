"""
api.py
~~~~~~
Cliente de /admin para la CLI, con urllib (sin httpx ni pydantic: arranque rápido).
Los errores salen como ApiError con el código de salida de la spec: 1 error,
2 uso incorrecto (422), 3 gateway inalcanzable.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import click

TIMEOUT_S = 5.0


class ApiError(click.ClickException):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _read_env_file(path: Path) -> dict[str, str]:
    # shortcut: KEY=VALUE con comillas opcionales; sin export, escapes ni multilínea.
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def resolve_token(explicit: str | None) -> str:
    """--token / JOTA_GW_TOKEN (ya en `explicit`), ADMIN_TOKEN y ~/.jota-gateway/config.env."""
    if explicit:
        return explicit
    if os.environ.get("ADMIN_TOKEN"):
        return os.environ["ADMIN_TOKEN"]
    config = Path.home() / ".jota-gateway" / "config.env"
    if config.is_file():
        token = _read_env_file(config).get("ADMIN_TOKEN")
        if token:
            return token
    raise ApiError(
        "Falta el token de administración: usa --token o la variable JOTA_GW_TOKEN.", 2
    )


def _detail(err: urllib.error.HTTPError) -> Any:
    try:
        return json.loads(err.read()).get("detail")
    except (ValueError, AttributeError):
        return None


def _validation_message(detail: Any) -> str:
    # Solo campo y mensaje: el `input` de pydantic llevaría el valor (p. ej. una clave).
    if not isinstance(detail, list):
        return str(detail or "Datos no válidos.")
    parts = []
    for item in detail:
        loc = [str(p) for p in item.get("loc", []) if p != "body"]
        parts.append(f"{'.'.join(loc) or 'petición'}: {item.get('msg', 'no válido')}")
    return "Datos no válidos: " + "; ".join(parts)


class Api:
    def __init__(self, url: str, token: str, timeout: float = TIMEOUT_S) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("X-Admin-Token", self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise self._http_error(e) from None
        except (urllib.error.URLError, OSError):
            host = urlsplit(self.url).netloc
            raise ApiError(
                f"El gateway no responde en {host}. ¿Está arrancado? Prueba `jota-gw doctor`.",
                3,
            ) from None
        return json.loads(raw) if raw else None

    @staticmethod
    def _http_error(e: urllib.error.HTTPError) -> ApiError:
        if e.code == 401:
            return ApiError("El token de administración no es válido.")
        detail = _detail(e)
        if e.code == 422:
            return ApiError(_validation_message(detail), 2)
        if e.code >= 500:
            return ApiError(f"Error del gateway ({e.code}).")
        return ApiError(str(detail or f"Error {e.code}."))


def from_context(ctx: click.Context) -> Api:
    obj = ctx.find_root().obj
    return Api(obj["url"], resolve_token(obj["token"]))
