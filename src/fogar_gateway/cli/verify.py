"""
verify.py
~~~~~~~~~
Verificadores de proveedores para `fogar-gw onboard`: cada uno prueba el servicio real con el
mismo cliente que usa el gateway y devuelve un `Verdict`. Nunca lanzan (salvo
`CancelledError`) ni llevan un secreto en `detail` ni en los logs. Arriba solo stdlib: lo
pesado se importa dentro de cada función (`fogar-gw --help` < 150 ms).
"""

from __future__ import annotations

import asyncio
import importlib
import ipaddress
import json
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
    "El asistente solo configura un OpenClaw en este equipo (o por un túnel SSH: "
    "`ssh -L 18789:127.0.0.1:18789 …`). Para uno de otra máquina, a mano: "
    "`fogar-gw config set OPENCLAW_HOST …` y, la primera vez, aprueba el gateway en OpenClaw "
    "con el `openclaw devices approve …` que enseña `fogar-gw doctor`."
)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: Literal[
        "ok", "unreachable", "rejected", "timeout", "not_loopback", "invalid", "pairing_required"
    ]
    detail: str  # frase en español para el usuario, sin secretos


def is_loopback(host: str) -> bool:
    """127.0.0.0/8, ::1 o `localhost`. Nunca resuelve DNS: un nombre que resuelva a
    127.0.0.1 no cuenta, para que un nombre remoto no pueda colarse por DNS."""
    if host.lower() == "localhost":
        return True
    if host.startswith("[") and host.endswith("]"):  # `[::1]`: OpenClawClient lo respeta
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def detect_openclaw(port: int = 18789) -> str | None:
    """Versión de OpenClaw si `GET http://127.0.0.1:{port}/healthz` da 2xx; None si nada
    contesta 2xx. OpenClaw 2026.11.1 contesta `{"ok":true,"status":"live"}`, sin versión
    (`dist/gateway-prewarm.js`): hoy devuelve "" (encontrado, versión desconocida). "" es
    falso: quien llame debe comprobar `is not None`, nunca la verdad del valor."""
    import httpx

    from fogar_gateway.services.reconnection import describe_error

    try:
        # Sin los proxies del entorno: una sonda a loopback nunca pasa por un proxy.
        async with httpx.AsyncClient(trust_env=False) as client:
            r = await client.get(f"http://127.0.0.1:{port}/healthz", timeout=DETECT_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — sonda: cualquier fallo es «no hay OpenClaw»
        logger.debug("detect_openclaw: %s", describe_error(e))
        return None
    return "" if r.is_success else None


class _Rejected(Exception):
    """El servicio contestó y rechazó la autenticación (y solo eso)."""


class _Unexpected(Exception):
    """El servicio contestó, pero no lo que dice su protocolo."""


class _Pending(Exception):
    """El servicio aceptó la clave, pero falta un paso del operador (aprobar el dispositivo)."""

    def __init__(self, detail: str) -> None:
        super().__init__()
        self.detail = detail


def _config_problem() -> Verdict | None:
    """`core.config` construye `Settings()` al importarse, y todo cliente lo importa: un
    config.env o una variable de entorno inválidos se convierten aquí en `invalid`, con el
    campo y el tipo de error, nunca el valor (puede ser un secreto)."""
    from pydantic import ValidationError
    from pydantic_settings import SettingsError

    from fogar_gateway.cli.config import _errors, _settings_error
    from fogar_gateway.services.reconnection import describe_error

    try:
        importlib.import_module("fogar_gateway.core.config")
    except ValidationError as e:
        problems = _errors(e)
    except SettingsError as e:
        problems = _settings_error(e)
    except Exception as e:  # noqa: BLE001 — frontera del verificador (p. ej. config.env ilegible)
        problems = [describe_error(e)]
    else:
        return None
    return Verdict(
        False,
        "invalid",
        f"La configuración del gateway no es válida ({'; '.join(problems)}). "
        "Revísala con `fogar-gw config validate`.",
    )


async def _check(
    name: str,
    address: str,
    deadline_s: float,
    connect: Callable[[], Awaitable[object]],
    close: Callable[[], Awaitable[None]],
    rejected_detail: str,
    ok_detail: Callable[[], str] | None = None,
) -> Verdict:
    """Un intento con plazo total. `connect` traduce los fallos de su cliente a `_Rejected`,
    `_Unexpected`, `_Pending` o `TimeoutError`; el resto es «no responde». `ok_detail` da la
    frase del éxito (por defecto, «responde en …»)."""
    from fogar_gateway.services.reconnection import describe_error
    from fogar_gateway.services.speech.elevenlabs.common import pin_websockets_logging

    pin_websockets_logging()  # `websockets` a DEBUG volcaría el frame que lleva el token
    try:
        async with asyncio.timeout(deadline_s):
            await connect()
    except _Rejected:
        return Verdict(False, "rejected", rejected_detail)
    except _Pending as e:
        return Verdict(True, "pairing_required", e.detail)
    except _Unexpected:
        return Verdict(
            False,
            "invalid",
            f"{name} dio una respuesta inesperada en {address}: comprueba que es el servicio correcto.",
        )
    except TimeoutError:
        return Verdict(False, "timeout", f"{name} no contestó a tiempo.")
    except Exception as e:  # noqa: BLE001 — frontera del verificador: todo fallo es un Verdict
        logger.debug("verify %s: %s", name, describe_error(e))
        return Verdict(False, "unreachable", f"{name} no responde en {address}.")
    finally:
        await close()  # fuera del plazo: un cierre lento no convierte un «ok» en «timeout»
    return Verdict(True, "ok", ok_detail() if ok_detail else f"{name} responde en {address}.")


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
    if (bad := _config_problem()) is not None:
        return bad
    from fogar_gateway.services.openclaw.client import (
        OpenClawClient,
        OrchestratorHandshakeError,
        OrchestratorPairingRequired,
        OrchestratorRejectedError,
        OrchestratorStartingError,
        approve_command,
    )
    from fogar_gateway.services.openclaw.dispatcher import FrameDispatcher
    from fogar_gateway.services.openclaw.registry import ClientRegistry, TurnRegistry

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
        except OrchestratorPairingRequired as e:  # OpenClaw comprueba el token antes del emparejamiento
            raise _Pending(
                "OpenClaw acepta el token, pero falta aprobar este gateway como dispositivo. En la "
                f"máquina de OpenClaw ejecuta `{approve_command(e.request_id)}`: el gateway se "
                "conecta solo en cuanto lo apruebes (míralo con `fogar-gw doctor`)."
            ) from None
        except OrchestratorRejectedError:
            raise _Rejected from None  # `ok:false` a `connect`
        except OrchestratorHandshakeError:
            raise _Unexpected from None  # frame malformado o que no es el reto de OpenClaw

    def granted() -> str:
        access = client.access
        how = " (emparejado como dispositivo)" if access.pairing == "paired" else ""
        if access.admin:
            return f"Conectado a OpenClaw en {host}:{port} con permisos de administración{how}."
        scopes = ", ".join(access.scopes or []) or "ninguno"
        return (
            f"Conectado a OpenClaw en {host}:{port}{how}, pero sin permisos de administración "
            f"(permisos: {scopes}). Hoy basta para hablar; las funciones de administración "
            "no estarán disponibles."
        )

    return await _check(
        "OpenClaw",
        f"{host}:{port}",
        OPENCLAW_VERIFY_TIMEOUT_S,
        connect,
        client.close,
        "OpenClaw rechazó el token.",
        granted,
    )


async def verify_fogar_stt(url: str, token: str) -> Verdict:
    name = "El servicio de STT"
    if _bad_url(url):
        return Verdict(False, "invalid", "La dirección del servicio de STT es host:puerto, sin ws://.")
    if (bad := _config_problem()) is not None:
        return bad
    from pydantic import ValidationError

    from fogar_gateway.services.transcriber_client import (
        TranscriberClient,
        TranscriberHandshakeError,
    )

    client = TranscriberClient(url, "onboard")

    async def connect() -> None:
        try:
            await client.connect(token=token)
        except TranscriberHandshakeError as e:
            # Solo AUTH_REQUIRED/AUTH_FAILED rechazan la clave (CONFIG_ERROR, unexpected_frame… no).
            raise (_Rejected if e.code.startswith("AUTH_") else _Unexpected) from None
        # Primer frame que no es JSON (también binario), ni un objeto, ni un TranscriberMessage.
        # Por nombre: un puerto inválido es un ValueError a secas de websockets.connect.
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError):
            raise _Unexpected from None

    return await _check(
        name, url, VERIFY_TIMEOUT_S, connect, client.close, _own_rejected(name, token)
    )


async def verify_fogar_tts(url: str, token: str) -> Verdict:
    name = "El servicio de TTS"
    if _bad_url(url):
        return Verdict(False, "invalid", "La dirección del servicio de TTS es host:puerto, sin ws://.")
    if (bad := _config_problem()) is not None:
        return bad
    from fogar_gateway.services.tts_client import TTSAuthError, TTSClient, TTSHandshakeError

    client = TTSClient(url, token, "onboard")

    async def connect() -> None:
        try:
            await client.connect()
        except TTSAuthError:
            raise _Rejected from None
        except TTSHandshakeError:
            raise _Unexpected from None  # cerró el `auth` o contestó algo que no es JSON/objeto

    return await _check(
        name, url, VERIFY_TIMEOUT_S, connect, client.close, _own_rejected(name, token)
    )


async def verify_elevenlabs(kind: Literal["stt", "tts"], api_key: str) -> Verdict:
    """`probe()` del proveedor real con una copia de `settings` que lleva esta clave (el
    singleton no se toca). `rejected` si el proveedor queda en su estado terminal (clave
    rechazada o cuota agotada); cualquier otro fallo es `unreachable`."""
    if not api_key.strip():
        return Verdict(False, "invalid", "Falta la clave de ElevenLabs.")
    if (bad := _config_problem()) is not None:
        return bad
    from pydantic import SecretStr

    from fogar_gateway.core.config import settings
    from fogar_gateway.services.reconnection import describe_error
    from fogar_gateway.services.speech.elevenlabs.stt import ElevenLabsSttProvider
    from fogar_gateway.services.speech.elevenlabs.tts import ElevenLabsTtsProvider

    provider: ElevenLabsSttProvider | ElevenLabsTtsProvider | None = None
    ok = False
    try:
        temp = settings.model_copy(update={"ELEVENLABS_API_KEY": SecretStr(api_key)})
        # El constructor fija `websockets` en INFO (api_key()): la cabecera xi-api-key no sale.
        provider = (ElevenLabsSttProvider if kind == "stt" else ElevenLabsTtsProvider)(temp)
        async with asyncio.timeout(VERIFY_TIMEOUT_S):
            ok = await provider.probe()
    except TimeoutError:
        return Verdict(False, "timeout", "ElevenLabs no contestó a tiempo.")
    except Exception as e:  # noqa: BLE001 — frontera del verificador (probe() ya no lanza)
        logger.debug("verify ElevenLabs: %s", describe_error(e))
    finally:
        if provider is not None:
            await provider.aclose()
    if ok:
        return Verdict(True, "ok", "ElevenLabs acepta la clave.")
    if provider is not None and provider._terminal:
        return Verdict(False, "rejected", "ElevenLabs rechazó la clave (o la cuota de la cuenta está agotada).")
    return Verdict(False, "unreachable", "ElevenLabs no responde.")
