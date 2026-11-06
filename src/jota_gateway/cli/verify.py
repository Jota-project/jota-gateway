"""
verify.py
~~~~~~~~~
Verificadores de proveedores para `jota-gw onboard`: cada uno prueba el servicio real con el
mismo cliente que usa el gateway y devuelve un `Verdict`. Nunca lanzan (salvo
`CancelledError`) ni llevan un secreto en `detail` ni en los logs. Arriba solo stdlib: lo
pesado se importa dentro de cada función (`jota-gw --help` < 150 ms).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

VERIFY_TIMEOUT_S = 15.0
# Cubre los 30 s de reintento de OpenClawClient.connect mientras OpenClaw arranca.
OPENCLAW_VERIFY_TIMEOUT_S = 35.0
DETECT_TIMEOUT_S = 3.0

NOT_LOOPBACK_DETAIL = (
    "Conectar con un OpenClaw de otra máquina necesita emparejar el gateway como dispositivo, "
    "y eso aún no existe. Usa un OpenClaw en este equipo o un túnel SSH "
    "(`ssh -L 18789:127.0.0.1:18789 …`)."
)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: Literal["ok", "unreachable", "rejected", "timeout", "not_loopback", "invalid"]
    detail: str  # frase en español para el usuario, sin secretos


def is_loopback(host: str) -> bool:
    """127.0.0.0/8, ::1 o `localhost`. Nunca resuelve DNS: un nombre que resuelva a
    127.0.0.1 no cuenta, para que un nombre remoto no pueda colarse por DNS."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def detect_openclaw(port: int = 18789) -> str | None:
    """Versión de OpenClaw si `GET http://127.0.0.1:{port}/healthz` da 2xx; None si nada
    contesta 2xx. OpenClaw 2026.11.1 contesta `{"ok":true,"status":"live"}`, sin versión
    (`dist/gateway-prewarm.js`): hoy devuelve "" (encontrado, versión desconocida)."""
    import httpx

    from jota_gateway.services.reconnection import describe_error

    try:
        # Sin los proxies del entorno: una sonda a loopback nunca pasa por un proxy.
        async with httpx.AsyncClient(trust_env=False) as client:
            r = await client.get(f"http://127.0.0.1:{port}/healthz", timeout=DETECT_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — sonda: cualquier fallo es «no hay OpenClaw»
        logger.debug("detect_openclaw: %s", describe_error(e))
        return None
    return "" if r.is_success else None


async def _check(
    name: str,
    address: str,
    deadline_s: float,
    connect: Callable[[], Awaitable[object]],
    close: Callable[[], Awaitable[None]],
    rejected: tuple[type[BaseException], ...],
    rejected_detail: str,
) -> Verdict:
    """Un intento con plazo total. `rejected`: el servicio contestó y rechazó la clave."""
    from jota_gateway.services.reconnection import describe_error
    from jota_gateway.services.speech.elevenlabs.common import pin_websockets_logging

    pin_websockets_logging()  # `websockets` a DEBUG volcaría el frame que lleva el token
    try:
        async with asyncio.timeout(deadline_s):
            await connect()
    except rejected:
        return Verdict(False, "rejected", rejected_detail)
    except TimeoutError:
        return Verdict(False, "timeout", f"{name} no contestó a tiempo.")
    except Exception as e:  # noqa: BLE001 — frontera del verificador: todo fallo es un Verdict
        logger.debug("verify %s: %s", name, describe_error(e))
        return Verdict(False, "unreachable", f"{name} no responde en {address}.")
    finally:
        await close()  # fuera del plazo: un cierre lento no convierte un «ok» en «timeout»
    return Verdict(True, "ok", f"{name} responde en {address}.")


def _own_rejected(name: str, token: str) -> str:
    if token:
        return f"{name} rechazó la clave."
    return f"{name} rechazó la conexión sin clave: este servicio exige clave."


def _bad_url(url: str) -> bool:
    return not url or "://" in url  # los ajustes guardan host:puerto, sin protocolo


async def verify_openclaw(host: str, port: int, token: str) -> Verdict:
    if not is_loopback(host):
        return Verdict(False, "not_loopback", NOT_LOOPBACK_DETAIL)
    if not 0 < port < 65536:
        return Verdict(False, "invalid", "El puerto de OpenClaw debe estar entre 1 y 65535.")
    from jota_gateway.services.openclaw.client import (
        OpenClawClient,
        OrchestratorHandshakeError,
        OrchestratorStartingError,
    )
    from jota_gateway.services.openclaw.dispatcher import FrameDispatcher
    from jota_gateway.services.openclaw.registry import ClientRegistry, TurnRegistry

    turn_registry = TurnRegistry()
    client = OpenClawClient(
        host=host,
        port=port,
        token=token,
        turn_registry=turn_registry,
        dispatcher=FrameDispatcher(turn_registry, ClientRegistry()),
    )

    async def connect() -> None:
        try:
            await client.connect()
        except OrchestratorStartingError:
            raise TimeoutError from None  # sigue arrancando: no es un token rechazado

    return await _check(
        "OpenClaw",
        f"{host}:{port}",
        OPENCLAW_VERIFY_TIMEOUT_S,
        connect,
        client.close,
        (OrchestratorHandshakeError,),
        "OpenClaw rechazó el token.",
    )


async def verify_jota_stt(url: str, token: str) -> Verdict:
    name = "El servicio de STT"
    if _bad_url(url):
        return Verdict(False, "invalid", "La dirección del servicio de STT es host:puerto, sin ws://.")
    from jota_gateway.services.transcriber_client import (
        TranscriberClient,
        TranscriberHandshakeError,
    )

    client = TranscriberClient(url, "onboard")
    return await _check(
        name,
        url,
        VERIFY_TIMEOUT_S,
        lambda: client.connect(token=token),
        client.close,
        (TranscriberHandshakeError,),
        _own_rejected(name, token),
    )


async def verify_jota_tts(url: str, token: str) -> Verdict:
    name = "El servicio de TTS"
    if _bad_url(url):
        return Verdict(False, "invalid", "La dirección del servicio de TTS es host:puerto, sin ws://.")
    from jota_gateway.services.tts_client import TTSAuthError, TTSClient

    client = TTSClient(url, token, "onboard")
    return await _check(
        name,
        url,
        VERIFY_TIMEOUT_S,
        client.connect,
        client.close,
        (TTSAuthError,),
        _own_rejected(name, token),
    )


async def verify_elevenlabs(kind: Literal["stt", "tts"], api_key: str) -> Verdict:
    """`probe()` del proveedor real con una copia de `settings` que lleva esta clave (el
    singleton no se toca). `rejected` si el proveedor queda en su estado terminal (clave
    rechazada o cuota agotada); cualquier otro fallo es `unreachable`."""
    if not api_key.strip():
        return Verdict(False, "invalid", "Falta la clave de ElevenLabs.")
    from pydantic import SecretStr

    from jota_gateway.core.config import settings
    from jota_gateway.services.reconnection import describe_error
    from jota_gateway.services.speech.elevenlabs.stt import ElevenLabsSttProvider
    from jota_gateway.services.speech.elevenlabs.tts import ElevenLabsTtsProvider

    temp = settings.model_copy(update={"ELEVENLABS_API_KEY": SecretStr(api_key)})
    # El constructor fija `websockets` en INFO (api_key()): la cabecera xi-api-key no sale.
    provider = (ElevenLabsSttProvider if kind == "stt" else ElevenLabsTtsProvider)(temp)
    ok = False
    try:
        async with asyncio.timeout(VERIFY_TIMEOUT_S):
            ok = await provider.probe()
    except TimeoutError:
        return Verdict(False, "timeout", "ElevenLabs no contestó a tiempo.")
    except Exception as e:  # noqa: BLE001 — frontera del verificador (probe() ya no lanza)
        logger.debug("verify ElevenLabs: %s", describe_error(e))
    finally:
        await provider.aclose()
    if ok:
        return Verdict(True, "ok", "ElevenLabs acepta la clave.")
    if provider._terminal:
        return Verdict(False, "rejected", "ElevenLabs rechazó la clave (o la cuota de la cuenta está agotada).")
    return Verdict(False, "unreachable", "ElevenLabs no responde.")
