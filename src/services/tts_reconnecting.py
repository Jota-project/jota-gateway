import logging

from src.services.reconnection import (
    ConnectionState,
    LazyBackoffGate,
    ServiceHealth,
    ServiceStatus,
    describe_error,
    health_attr,
)
from src.services.tts_client import TTSClient

logger = logging.getLogger(__name__)


class ReconnectingTTSClient:
    """Gates per-turn TTSClient connection attempts behind a lazy backoff.

    Unlike Transcriber/OpenClaw there is no persistent connection to hold
    between turns — TTSClient is deliberately created fresh every turn (see
    CLAUDE.md). This is a process-level singleton (TTS_WS_URL is shared
    across all sessions) that remembers recent failures so a down TTS
    backend isn't hammered with a fresh failed connect on every turn across
    every concurrent session.

    Concurrent turns share this instance; the backoff bookkeeping lives in
    `LazyBackoffGate`, which orders overlapping attempts' outcomes (#152).
    """

    state = health_attr("state")
    on_state_change = health_attr("on_state_change")

    name = "jota"

    def __init__(
        self,
        url: str,
        token: str,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ) -> None:
        self._url = url
        self._token = token
        self._gate = LazyBackoffGate(initial_backoff, max_backoff)
        self._health = ServiceHealth("tts", ConnectionState.CONNECTED)

    async def connect(
        self, voice: str | None, speed: float | None, client_id: str
    ) -> TTSClient | None:
        """Returns a connected TTSClient, or None if the backoff window
        hasn't elapsed yet or the attempt itself fails."""
        token = self._gate.begin()
        if token is None:
            return None

        client = TTSClient(url=self._url, token=self._token, client_id=client_id)
        try:
            await client.connect(voice=voice, speed=speed)
        except Exception as e:
            self._record_failure(token, describe_error(e))
            return None

        self._record_success()
        return client

    def _record_failure(self, token: int, error: str) -> None:
        if not self._gate.record_failure(token):
            logger.debug("TTS connect failure ignored (same outage / stale attempt): %s", error)
            return
        self._health.mark_attempt_failed(error)
        logger.warning("TTS connect failed (attempt %d): %s", self._health.reconnect_attempts, error)
        self._health.set_state(ConnectionState.RECONNECTING)

    def _record_success(self) -> None:
        self._gate.record_success()
        self._health.mark_connected()

    def status(self) -> ServiceStatus:
        return self._health.status()

    # --- Puerto TextToSpeechProvider (src/services/speech/ports.py) ---

    async def probe(self) -> bool:
        return await TTSClient.ping(self._url)

    def resolve_voice(self, configured: str | None) -> str | None:
        return configured

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        return None
