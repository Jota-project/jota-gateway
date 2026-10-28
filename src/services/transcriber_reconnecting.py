import asyncio
import logging

from src.services.reconnection import (
    ConnectionState,
    ServiceHealth,
    ServiceStatus,
    describe_error,
    health_attr,
    reconnect_with_backoff,
    traceback_no_message,
)
from src.services.speech.ports import SttCallbacks, SttParams
from src.services.transcriber_client import TranscriberClient

logger = logging.getLogger(__name__)


class ReconnectingTranscriberClient:
    """Wraps TranscriberClient with exponential-backoff reconnection.

    One instance per audio session: it is the jota `SttSession` (built by
    `JotaSttProvider.open_session()`). Delegates send_audio/send_end/close to the inner
    client unchanged, so bridge.py's call sites don't need to know reconnection exists.
    """

    state = health_attr("state")
    on_state_change = health_attr("on_state_change")
    _last_error = health_attr("last_error")

    def __init__(
        self,
        url: str,
        client_id: str,
        callbacks: SttCallbacks,
        params: SttParams,
        token: str = "",
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
        max_duration: float = 300.0,
    ) -> None:
        self._client = TranscriberClient(url=url, client_id=client_id)
        self._client_id = client_id
        self._callbacks = callbacks
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._max_duration = max_duration
        self._health = ServiceHealth("transcriber", ConnectionState.DEGRADED)
        self._closed = False
        self._run_task: asyncio.Task | None = None
        self._language = params.language
        self._token = token
        self._vad_thold = params.vad_threshold

    @property
    def last_transcript_at(self) -> float | None:
        return self._client._last_transcription_at

    def _set_state(self, state: ConnectionState) -> None:
        self._health.set_state(state)

    async def start(self) -> None:
        """Attempts the initial connection. Never raises: on failure this
        just leaves state=RECONNECTING for run() to pick up in the background."""
        if self._closed:
            return
        try:
            await self._client.connect(
                language=self._language, token=self._token, vad_thold=self._vad_thold
            )
            self._health.mark_connected()
        except Exception as e:
            self._last_error = describe_error(e)
            logger.error(
                "[%s] transcriber initial connect failed: %s", self._client_id, describe_error(e)
            )
            self._set_state(ConnectionState.RECONNECTING)
        if self._closed:
            # close() llegó mientras connect() estaba en vuelo: el socket que acaba de
            # abrirse no tiene dueño, se cierra aquí (close() es idempotente).
            await self._client.close()

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

    async def run(self) -> None:
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
                cb = self._callbacks
                await self._client.listen_loop(
                    cb.on_transcription, cb.on_warning, cb.on_status, cb.on_incomplete
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

    def usage(self) -> dict[str, float]:
        return {}  # servicio propio: sin coste por uso que contabilizar
