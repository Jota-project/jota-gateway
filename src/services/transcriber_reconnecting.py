import asyncio
import logging
from collections.abc import Awaitable, Callable

from src.services.reconnection import (
    ConnectionState,
    ServiceHealth,
    ServiceStatus,
    describe_error,
    health_attr,
    reconnect_with_backoff,
    traceback_no_message,
)
from src.services.transcriber_client import TranscriberClient

logger = logging.getLogger(__name__)


class ReconnectingTranscriberClient:
    """Wraps TranscriberClient with exponential-backoff reconnection.

    One instance per audio session (constructed in JotaBridge.connect_internal_services()).
    Delegates send_audio/send_end/close to the inner client unchanged, so bridge.py's
    call sites for those don't need to know reconnection exists.
    """

    state = health_attr("state")
    on_state_change = health_attr("on_state_change")
    _connected_at = health_attr("connected_at")
    _reconnect_attempts = health_attr("reconnect_attempts")
    _last_error = health_attr("last_error")

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
        self._health = ServiceHealth("transcriber", ConnectionState.DEGRADED)
        self._closed = False
        self._run_task: asyncio.Task | None = None
        self._language = "es"
        self._token = ""
        self._vad_thold = 0.0

    @property
    def last_transcript_at(self) -> float | None:
        return self._client._last_transcription_at

    @property
    def _last_transcription_at(self) -> float | None:
        return self.last_transcript_at

    def _set_state(self, state: ConnectionState) -> None:
        self._health.set_state(state)

    async def connect(self, language: str = "es", token: str = "", vad_thold: float = 0.0) -> None:
        """Attempts the initial connection. Never raises: on failure this
        just leaves state=RECONNECTING for run() to pick up in the background."""
        self._language, self._token, self._vad_thold = language, token, vad_thold
        try:
            await self._client.connect(language=language, token=token, vad_thold=vad_thold)
            self._health.mark_connected()
        except Exception as e:
            self._last_error = describe_error(e)
            logger.error(
                "[%s] transcriber initial connect failed: %s", self._client_id, describe_error(e)
            )
            self._set_state(ConnectionState.RECONNECTING)

    async def send_audio(self, audio_bytes: bytes) -> None:
        await self._client.send_audio(audio_bytes)

    async def send_end(self) -> None:
        await self._client.send_end()

    async def close(self) -> None:
        self._closed = True
        current_task = asyncio.current_task()
        if (
            self._run_task is not None
            and not self._run_task.done()
            and self._run_task is not current_task
        ):
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
        await self._client.close()

    async def run(
        self,
        on_transcription_callback: Callable[[str, bool], Awaitable[None]],
        on_warning_callback: Callable[[str, str | None], Awaitable[None]] | None = None,
        on_status_callback: Callable[[str, str | None], Awaitable[None]] | None = None,
        on_incomplete_callback: Callable[[str | None], Awaitable[None]] | None = None,
    ) -> None:
        """Supervises the listen loop for the session's lifetime: listens,
        and on an unexpected drop, reconnects with backoff and resumes
        listening — until close() is called or reconnection is exhausted
        (DEGRADED, in which case this returns and the session continues
        without a working transcriber for the rest of its lifetime)."""
        self._run_task = asyncio.current_task()
        while not self._closed:
            if self.state != ConnectionState.CONNECTED:
                recovered = await self._reconnect_loop()
                if not recovered:
                    return

            self._client._dropped_unexpectedly = False
            try:
                await self._client.listen_loop(
                    on_transcription_callback,
                    on_warning_callback,
                    on_status_callback,
                    on_incomplete_callback,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Unexpected failure (e.g. a bug in the transcription callback), not a
                # connection drop: don't reconnect, don't close the session — degrade
                # so the client gets `status unavailable` via on_state_change (#131).
                self._last_error = describe_error(e)
                logger.error(
                    "[%s] transcriber listen loop crashed (%s) — DEGRADED.",
                    self._client_id,
                    self._last_error,
                )
                logger.debug("[%s] listen loop: %s", self._client_id, traceback_no_message(e))
                self._set_state(ConnectionState.DEGRADED)
                return

            if self._closed:
                return
            if not self._client._dropped_unexpectedly:
                # Clean close (code 1000) — deliberate (our own close(), or a
                # normal server-side session end). Not a failure: don't reconnect.
                return
            self._set_state(ConnectionState.RECONNECTING)

    async def _reconnect_loop(self) -> bool:
        async def attempt() -> None:
            await self._client.connect(
                language=self._language, token=self._token, vad_thold=self._vad_thold
            )

        return await reconnect_with_backoff(
            attempt,
            self._health,
            initial_backoff=self._initial_backoff,
            max_backoff=self._max_backoff,
            max_duration=self._max_duration,
            label=f"{self._client_id} transcriber",
        )

    def status(self) -> ServiceStatus:
        return self._health.status()
