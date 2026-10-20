"""Adaptador de los servicios propios (jota-transcriber y jota-speaker) a los puertos de voz.

Delgado a propósito: los módulos del cliente jota (`transcriber_*`, `tts_*`) no cambian de
sitio ni de contrato; aquí solo se les pone la forma del puerto.
"""

from __future__ import annotations

import logging

from src.models.schemas import Client
from src.services.reconnection import ConnectionState, ServiceStatus
from src.services.speech.ports import SttCallbacks, SttCapabilities, SttParams
from src.services.transcriber_client import TranscriberClient
from src.services.transcriber_reconnecting import ReconnectingTranscriberClient
from src.services.tts_reconnecting import ReconnectingTTSClient

logger = logging.getLogger(__name__)

# El TTS jota ya es un proveedor: el wrapper actual implementa el puerto.
JotaTtsProvider = ReconnectingTTSClient


class JotaSttSession:
    """Una sesión de audio: delegación fina sobre `ReconnectingTranscriberClient`."""

    def __init__(
        self,
        inner: ReconnectingTranscriberClient,
        params: SttParams,
        callbacks: SttCallbacks,
        token: str,
        on_closed=None,
    ) -> None:
        self._inner = inner
        self._params = params
        self._callbacks = callbacks
        self._token = token
        self._on_closed = on_closed
        self._closed = False

    @property
    def state(self) -> ConnectionState:
        return self._inner.state

    @state.setter
    def state(self, value: ConnectionState) -> None:
        self._inner.state = value

    @property
    def on_state_change(self):
        return self._inner.on_state_change

    @on_state_change.setter
    def on_state_change(self, value) -> None:
        self._inner.on_state_change = value

    @property
    def last_transcript_at(self) -> float | None:
        return self._inner.last_transcript_at

    async def start(self) -> None:
        if self._closed:
            return
        await self._inner.connect(
            language=self._params.language,
            token=self._token,
            vad_thold=self._params.vad_threshold,
        )
        if self._closed:
            # close() llegó mientras connect() estaba en vuelo: el socket que acaba de
            # abrirse no tiene dueño, se cierra aquí (close() es idempotente).
            await self._inner.close()

    async def send_audio(self, frame: bytes) -> None:
        await self._inner.send_audio(frame)

    async def send_end(self) -> None:
        await self._inner.send_end()

    async def run(self) -> None:
        await self._inner.run(
            on_transcription_callback=self._callbacks.on_transcription,
            on_warning_callback=self._callbacks.on_warning,
            on_status_callback=self._callbacks.on_status,
            on_incomplete_callback=self._callbacks.on_incomplete,
        )

    async def close(self) -> None:
        self._closed = True
        try:
            await self._inner.close()
        finally:
            callback, self._on_closed = self._on_closed, None  # una sola vez
            if callback is not None:
                await callback()

    def usage(self) -> dict[str, float]:
        return {}  # servicio propio: sin coste por uso que contabilizar


class JotaSttProvider:
    """STT propio. La salud real es por sesión, así que el estado de proveedor es siempre
    CONNECTED; `probe()` consulta `/ready` en vivo (local y gratis)."""

    name = "jota"
    capabilities = SttCapabilities(partials=True, warmup_s=0.0)

    def __init__(self, settings) -> None:
        self._settings = settings
        self.state = ConnectionState.CONNECTED
        self.on_state_change = None
        self._last_probe: bool | None = None
        self._open_sessions = 0
        self._retired = False

    def open_session(
        self, *, client: Client, params: SttParams, callbacks: SttCallbacks
    ) -> JotaSttSession:
        s = self._settings
        inner = ReconnectingTranscriberClient(
            url=s.TRANSCRIBER_WS_URL,
            client_id=client.id,
            initial_backoff=s.TRANSCRIBER_RECONNECT_INITIAL_BACKOFF,
            max_backoff=s.TRANSCRIBER_RECONNECT_MAX_BACKOFF,
            max_duration=s.TRANSCRIBER_RECONNECT_MAX_DURATION,
        )
        self._open_sessions += 1
        return JotaSttSession(
            inner, params, callbacks, token=client.client_key, on_closed=self._session_closed
        )

    async def _session_closed(self) -> None:
        self._open_sessions -= 1
        if self._retired and self._open_sessions <= 0:
            await self.aclose()

    async def retire(self) -> None:
        """Proveedor sustituido en caliente: se cierra al cerrar su última sesión abierta
        (ya, si no tiene ninguna)."""
        self._retired = True
        if self._open_sessions <= 0:
            await self.aclose()

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
