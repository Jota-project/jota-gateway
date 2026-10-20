"""Utilidades comunes de los adaptadores ElevenLabs: URLs, logging y errores normalizados.

Nada de aquí registra ni devuelve el texto de un error del proveedor: solo la clase
(`auth`/`quota`/`capacity`/`other`).
"""

from __future__ import annotations

import logging
from typing import Literal
from urllib.parse import quote, urlencode

from websockets.exceptions import ConnectionClosed, InvalidStatus

ErrorKind = Literal["auth", "quota", "capacity", "other"]

_QUOTA_WORDS = ("quota", "credits", "insufficient")  # cuota agotada: terminal, como auth
_TIME_LIMIT_WORDS = ("session_time_limit", "time limit")  # tope de la sesión: solo reabrir
_CAPACITY_WORDS = ("limit", "busy")
_AUTH_WORDS = (
    "api key",
    "unauthorized",
    "forbidden",
    "authentication",
    "invalid key",
    "permission",
)


def pin_websockets_logging() -> None:
    """`websockets` a DEBUG vuelca cabeceras (incluida `xi-api-key`) y cada frame de texto:
    se fija el logger en INFO como mínimo. Idempotente; nunca baja un nivel ya más alto."""
    for name in ("websockets", "websockets.client"):
        lg = logging.getLogger(name)
        lg.setLevel(max(lg.level, logging.INFO))


def ws_url(base: str, path: str, params: dict[str, str | int] | None = None) -> str:
    """`base` (con o sin esquema y sin barra final) + `path` + query. Sin esquema -> wss."""
    base = base.strip().rstrip("/")
    if "://" not in base:
        base = f"wss://{base}"
    url = base + "/" + quote(path.lstrip("/"), safe="/")
    if params:
        url += "?" + urlencode(params)
    return url


def https_base(base: str) -> str:
    """Base para la API REST: wss:// -> https://, ws:// -> http://."""
    base = base.strip().rstrip("/")
    if base.startswith("wss://"):
        return "https://" + base[len("wss://") :]
    if base.startswith("ws://"):
        return "http://" + base[len("ws://") :]
    return base if "://" in base else f"https://{base}"


def classify_close(code: int | None, reason: str | None) -> ErrorKind:
    text = (reason or "").lower()
    if any(w in text for w in _QUOTA_WORDS):
        return "quota"
    if any(w in text for w in _TIME_LIMIT_WORDS):
        return "other"
    if any(w in text for w in _CAPACITY_WORDS):
        return "capacity"
    # 1008 a secas NO es auth: ElevenLabs también lo usa para timeouts de entrada/inactividad
    # o voz/modelo inválidos, y "auth" degrada el proveedor entero de forma terminal.
    if any(w in text for w in _AUTH_WORDS):
        return "auth"
    return "other"


def classify_exception(exc: BaseException) -> ErrorKind:
    if isinstance(exc, InvalidStatus):
        status = exc.response.status_code
        if status in (401, 403):
            return "auth"
        if status == 429:
            return "capacity"
        return "other"
    if isinstance(exc, ConnectionClosed):
        frame = exc.rcvd
        return classify_close(frame.code if frame else None, frame.reason if frame else None)
    return "other"
