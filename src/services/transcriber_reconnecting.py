import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from src.services.reconnection import ConnectionState, ServiceStatus
from src.services.transcriber_client import TranscriberClient

logger = logging.getLogger(__name__)


class ReconnectingTranscriberClient:
    """Wraps TranscriberClient with exponential-backoff reconnection.

    One instance per audio session (constructed in JotaBridge.connect_internal_services()).
    Delegates send_audio/send_end/close to the inner client unchanged, so bridge.py's
    call sites for those don't need to know reconnection exists.
    """

    def __init__(
        self,
        url: str,
        client_id: str,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
        max_duration: float = 300.0,
    ) -> None:
        self._client = TranscriberClient(url=url, client_id=client_id)
        self._client_id = client_id
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._max_duration = max_duration
        self.state = ConnectionState.DEGRADED
        self._connected_at: Optional[datetime] = None
        self._reconnect_attempts = 0
        self._last_error: Optional[str] = None
        self._closed = False
        self.on_state_change: Optional[Callable[[ConnectionState], None]] = None
        self._language = "es"
        self._token = ""
        self._vad_thold = 0.0

    @property
    def _last_transcription_at(self) -> Optional[float]:
        return self._client._last_transcription_at

    def _set_state(self, state: ConnectionState) -> None:
        self.state = state
        if self.on_state_change:
            self.on_state_change(state)

    async def connect(self, language: str = "es", token: str = "", vad_thold: float = 0.0) -> None:
        """Attempts the initial connection. Never raises: on failure this
        just leaves state=RECONNECTING for run() to pick up in the background."""
        self._language, self._token, self._vad_thold = language, token, vad_thold
        try:
            await self._client.connect(language=language, token=token, vad_thold=vad_thold)
            self._set_state(ConnectionState.CONNECTED)
            self._connected_at = datetime.now(timezone.utc)
            self._reconnect_attempts = 0
            self._last_error = None
        except Exception as e:
            self._last_error = str(e)
            logger.error("[%s] transcriber initial connect failed: %s", self._client_id, e)
            self._set_state(ConnectionState.RECONNECTING)

    async def send_audio(self, audio_bytes: bytes) -> None:
        await self._client.send_audio(audio_bytes)

    async def send_end(self) -> None:
        await self._client.send_end()

    async def close(self) -> None:
        self._closed = True
        await self._client.close()

    async def run(
        self,
        on_transcription_callback: Callable[[str, bool], Awaitable[None]],
        on_warning_callback: Optional[Callable[[str, Optional[str]], Awaitable[None]]] = None,
    ) -> None:
        """Supervises the listen loop for the session's lifetime: listens,
        and on an unexpected drop, reconnects with backoff and resumes
        listening — until close() is called or reconnection is exhausted
        (DEGRADED, in which case this returns and the session continues
        without a working transcriber for the rest of its lifetime)."""
        while not self._closed:
            if self.state != ConnectionState.CONNECTED:
                recovered = await self._reconnect_loop()
                if not recovered:
                    return

            self._client._dropped_unexpectedly = False
            await self._client.listen_loop(on_transcription_callback, on_warning_callback)

            if self._closed:
                return
            if not self._client._dropped_unexpectedly:
                # Clean close (code 1000) — deliberate (our own close(), or a
                # normal server-side session end). Not a failure: don't reconnect.
                return
            self._set_state(ConnectionState.RECONNECTING)

    async def _reconnect_loop(self) -> bool:
        start = time.monotonic()
        backoff = self._initial_backoff
        while True:
            try:
                await self._client.connect(
                    language=self._language, token=self._token, vad_thold=self._vad_thold
                )
                self._set_state(ConnectionState.CONNECTED)
                self._connected_at = datetime.now(timezone.utc)
                self._reconnect_attempts = 0
                logger.info("[%s] transcriber reconnected.", self._client_id)
                return True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._reconnect_attempts += 1
                self._last_error = str(e)
                logger.warning(
                    "[%s] transcriber reconnect attempt %d failed: %s",
                    self._client_id, self._reconnect_attempts, e,
                )

            if time.monotonic() - start >= self._max_duration:
                self._set_state(ConnectionState.DEGRADED)
                logger.warning("[%s] transcriber reconnect exhausted — DEGRADED.", self._client_id)
                return False

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._max_backoff)

    def status(self) -> ServiceStatus:
        return ServiceStatus(
            name="transcriber",
            state=self.state,
            connected_at=self._connected_at,
            reconnect_attempts=self._reconnect_attempts,
            last_error=self._last_error,
        )
