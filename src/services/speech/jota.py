"""Adaptador de los servicios propios (jota-transcriber y jota-speaker) a los puertos de voz.

Delgado a propósito: `ReconnectingTranscriberClient` ya es la `SttSession` y
`ReconnectingTTSClient` el `TextToSpeechProvider`; aquí solo está el proveedor STT.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from src.models.schemas import Client
from src.services.reconnection import ConnectionState, ServiceStatus
from src.services.speech.ports import SttCallbacks, SttCapabilities, SttParams
from src.services.transcriber_client import TranscriberClient
from src.services.transcriber_reconnecting import ReconnectingTranscriberClient
from src.services.tts_reconnecting import ReconnectingTTSClient

logger = logging.getLogger(__name__)

# El TTS jota ya es un proveedor: el wrapper actual implementa el puerto.
JotaTtsProvider = ReconnectingTTSClient


class JotaSttProvider:
    """STT propio. La salud real es por sesión, así que el estado de proveedor es siempre
    CONNECTED; `probe()` consulta `/ready` en vivo (local y gratis)."""

    name = "jota"
    capabilities = SttCapabilities(partials=True, warmup_s=0.0)

    def __init__(self, settings) -> None:
        self._settings = settings
        self.state = ConnectionState.CONNECTED
        self.on_state_change: Callable[[ConnectionState], None] | None = None
        self._last_probe: bool | None = None

    def open_session(
        self, *, client: Client, params: SttParams, callbacks: SttCallbacks
    ) -> ReconnectingTranscriberClient:
        s = self._settings
        return ReconnectingTranscriberClient(
            url=s.TRANSCRIBER_WS_URL,
            client_id=client.id,
            callbacks=callbacks,
            params=params,
            token=s.TRANSCRIBER_TOKEN,
            initial_backoff=s.TRANSCRIBER_RECONNECT_INITIAL_BACKOFF,
            max_backoff=s.TRANSCRIBER_RECONNECT_MAX_BACKOFF,
            max_duration=s.TRANSCRIBER_RECONNECT_MAX_DURATION,
        )

    async def probe(self) -> bool:
        self._last_probe = await TranscriberClient.ping(self._settings.TRANSCRIBER_WS_URL)
        return self._last_probe

    def status(self) -> ServiceStatus:
        """Último resultado de `probe()` (CONNECTED si nunca se sondeó)."""
        state = (
            ConnectionState.DEGRADED if self._last_probe is False else ConnectionState.CONNECTED
        )
        return ServiceStatus(
            name="transcriber",
            state=state,
            connected_at=None,
            reconnect_attempts=0,
            last_error=None,
        )

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        return None
