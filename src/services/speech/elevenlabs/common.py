"""Utilidades comunes de los adaptadores ElevenLabs: clave, URLs, sockets, sonda con caché y
errores normalizados.

Nada de aquí registra ni devuelve el texto de un error del proveedor: solo la clase
(`auth`/`quota`/`capacity`/`other`) o el nombre del tipo de excepción.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Literal
from urllib.parse import quote, urlencode

from websockets.exceptions import ConnectionClosed, InvalidStatus

from src.services.reconnection import describe_error

logger = logging.getLogger(__name__)

ErrorKind = Literal["auth", "quota", "capacity", "other"]

HEALTH_TTL_S = 60.0  # caché de probe(): cada sonda abre una conexión al proveedor
CLOSE_GRACE_S = 0.5  # cierre limpio de un socket; si no, websockets aborta el transporte

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


def api_key(settings) -> str:
    """`ELEVENLABS_API_KEY` sin espacios (ValueError si falta) y `websockets` fijado en INFO."""
    secret = settings.ELEVENLABS_API_KEY
    key = secret.get_secret_value().strip() if secret is not None else ""
    if not key:
        raise ValueError("ELEVENLABS_API_KEY")
    pin_websockets_logging()
    return key


async def close_ws(ws) -> None:
    try:
        await asyncio.wait_for(ws.close(), CLOSE_GRACE_S)
    except Exception:  # incluye TimeoutError
        pass


async def json_messages(ws) -> AsyncIterator[dict]:
    """Los frames de texto que son objetos JSON; el resto se descarta en silencio."""
    async for raw in ws:
        if isinstance(raw, bytes):
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict):
            yield data


class ProbedProvider:
    """`probe()` con caché de `HEALTH_TTL_S`, `start()` que nunca lanza y `aclose()`.
    Cada adaptador pone `_probe_once()` (None = sin red, p. ej. en backoff: no se cachea)."""

    _closed = False
    _terminal = False  # credenciales rechazadas o cuota agotada: hasta reiniciar o cambiar
    _probe_at: float | None = None
    _probe_ok = False

    async def _probe_once(self) -> bool | None:
        raise NotImplementedError

    async def probe(self) -> bool:
        if self._closed or self._terminal:
            return False
        if self._probe_at is not None and time.monotonic() - self._probe_at < HEALTH_TTL_S:
            return self._probe_ok
        ok = await self._probe_once()
        if ok is None:
            return False
        self._probe_ok, self._probe_at = ok, time.monotonic()
        return ok

    async def start(self) -> None:
        try:
            await self.probe()
        except Exception as e:
            logger.warning("%r: probe de arranque falló (%s)", self, describe_error(e))

    async def aclose(self) -> None:
        self._closed = True


def ws_url(base: str, path: str, params: dict[str, str | int | float] | None = None) -> str:
    """`base` (con o sin esquema y sin barra final) + `path` + query. Sin esquema -> wss."""
    base = base.strip().rstrip("/")
    if "://" not in base:
        base = f"wss://{base}"
    url = base + "/" + quote(path.lstrip("/"), safe="/")
    if params:
        url += "?" + urlencode(params)
    return url


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
