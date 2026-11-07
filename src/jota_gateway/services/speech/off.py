"""Voz desactivada (`STT_PROVIDER`/`TTS_PROVIDER` = `off`): la sesión sigue solo en texto.

Nunca toca la red. El bridge lo ve como un servicio caído que ya no reintenta: avisa con
el `status unavailable` de siempre y `live_capabilities` en false, sin cerrar la sesión.
"""

from __future__ import annotations

from collections.abc import Callable

from jota_gateway.models.schemas import Client
from jota_gateway.services.reconnection import ConnectionState, ServiceStatus
from jota_gateway.services.speech.ports import SttCallbacks, SttCapabilities, SttParams


def _disabled(name: str) -> ServiceStatus:
    return ServiceStatus(
        name=name,
        state=ConnectionState.DEGRADED,
        connected_at=None,
        reconnect_attempts=0,
        last_error="disabled",
    )


class OffSttSession:
    """Descarta el audio; DEGRADED desde el inicio, así el watchdog de silencio no vigila."""

    def __init__(self) -> None:
        self.state = ConnectionState.DEGRADED
        self.on_state_change: Callable[[ConnectionState], None] | None = None

    @property
    def last_transcript_at(self) -> float | None:
        return None

    async def start(self) -> None:
        return None

    async def send_audio(self, frame: bytes) -> None:
        return None

    async def send_end(self) -> None:
        return None

    async def run(self) -> None:
        return None

    async def close(self) -> None:
        return None


class OffSttProvider:
    name = "off"
    capabilities = SttCapabilities(partials=False, warmup_s=0.0)

    def __init__(self) -> None:
        self.state = ConnectionState.DEGRADED
        self.on_state_change: Callable[[ConnectionState], None] | None = None

    def open_session(
        self, *, client: Client, params: SttParams, callbacks: SttCallbacks
    ) -> OffSttSession:
        return OffSttSession()

    async def probe(self) -> bool:
        return False

    def status(self) -> ServiceStatus:
        return _disabled("transcriber")

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class OffTtsProvider:
    name = "off"

    def __init__(self) -> None:
        self.state = ConnectionState.DEGRADED
        self.on_state_change: Callable[[ConnectionState], None] | None = None

    async def connect(self, voice: str | None, speed: float | None, client_id: str) -> None:
        return None  # como un TTS caído: el turno sigue sin audio

    async def probe(self) -> bool:
        return False

    def status(self) -> ServiceStatus:
        return _disabled("tts")

    def resolve_voice(self, configured: str | None) -> str | None:
        return None

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        return None
