import logging
import time
from datetime import datetime, timezone
from typing import Optional

from src.services.reconnection import ConnectionState, ServiceStatus
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
    """

    def __init__(
        self,
        url: str,
        token: str,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ) -> None:
        self._url = url
        self._token = token
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._backoff = initial_backoff
        self._last_failure_at: Optional[float] = None
        self._last_success_at: Optional[datetime] = None
        self.state = ConnectionState.CONNECTED
        self._reconnect_attempts = 0
        self._last_error: Optional[str] = None

    def _should_attempt(self) -> bool:
        if self._last_failure_at is None:
            return True
        return (time.monotonic() - self._last_failure_at) >= self._backoff

    async def connect(
        self, voice: Optional[str], speed: Optional[float], client_id: str
    ) -> Optional[TTSClient]:
        """Returns a connected TTSClient, or None if the backoff window
        hasn't elapsed yet or the attempt itself fails."""
        if not self._should_attempt():
            return None

        client = TTSClient(url=self._url, token=self._token, client_id=client_id)
        try:
            await client.connect(voice=voice, speed=speed)
        except Exception as e:
            self._record_failure(str(e))
            return None

        self._record_success()
        return client

    def _record_failure(self, error: str) -> None:
        self._last_failure_at = time.monotonic()
        self._reconnect_attempts += 1
        self._last_error = error
        self._backoff = min(self._backoff * 2, self._max_backoff)
        self.state = ConnectionState.RECONNECTING
        logger.warning("TTS connect failed (attempt %d): %s", self._reconnect_attempts, error)

    def _record_success(self) -> None:
        self._last_failure_at = None
        self._backoff = self._initial_backoff
        self._reconnect_attempts = 0
        self._last_error = None
        self._last_success_at = datetime.now(timezone.utc)
        self.state = ConnectionState.CONNECTED

    def status(self) -> ServiceStatus:
        return ServiceStatus(
            name="tts",
            state=self.state,
            connected_at=self._last_success_at,
            reconnect_attempts=self._reconnect_attempts,
            last_error=self._last_error,
        )
