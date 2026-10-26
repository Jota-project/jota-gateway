import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncGenerator

from src.services.openclaw.client import (
    ORCHESTRATOR_ERROR,
    ORCHESTRATOR_UNAVAILABLE_ERROR,
    OpenClawClient,
)
from src.services.openclaw.models import GatewayInfo
from src.services.protocol import OrchestratorEvent
from src.services.reconnection import (
    ConnectionState,
    ServiceHealth,
    ServiceStatus,
    describe_error,
    health_attr,
    reconnect_with_backoff,
    traceback_no_message,
)

logger = logging.getLogger(__name__)


class ReconnectingOpenClawClient:
    """Wraps OpenClawClient with exponential backoff reconnection.

    Implements OrchestratorProtocol so JotaBridge needs no changes.
    Exposes gateway_info after successful connect.
    """

    state = health_attr("state")
    on_state_change = health_attr("on_state_change")
    _connected_at = health_attr("connected_at")
    _reconnect_attempts = health_attr("reconnect_attempts")
    _last_error = health_attr("last_error")

    def __init__(
        self,
        client: OpenClawClient,
        name: str,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
        max_duration: float = 300.0,
    ) -> None:
        self._client = client
        self._name = name
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._max_duration = max_duration
        self._health = ServiceHealth(name, ConnectionState.DEGRADED)
        self.gateway_info: GatewayInfo | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_job_id: str = ""
        self._reconnect_exhausted: bool = False
        self._slow_retry_task: asyncio.Task | None = None
        # Register disconnect callback so the inner client notifies us on unexpected drops.
        self._client.on_disconnect = self._handle_disconnect

    def get_name(self) -> str:
        return self._name

    async def connect(self) -> None:
        try:
            self.gateway_info = await self._client.connect()
            self._health.mark_connected()
            self._reconnect_exhausted = False
        except Exception as e:
            # Type/stable code only (#228): the text can carry OpenClaw-provided content.
            self._last_error = describe_error(e)
            logger.error(
                "[%s] initial connect failed (%s) — starting retry", self._name, self._last_error
            )
            logger.debug("[%s] connect traceback (no message): %s", self._name, traceback_no_message(e))
            self._ensure_reconnecting()

    async def close(self) -> None:
        slow = self._slow_retry_task
        self._cancel_slow_retry()
        if slow is not None:
            await asyncio.gather(slow, return_exceptions=True)
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        await self._client.close()

    async def ping(self) -> bool:
        if self.state != ConnectionState.CONNECTED:
            if self.state == ConnectionState.DEGRADED:
                self._ensure_reconnecting()
            return False
        return await self._client.ping()

    async def stream_response(
        self,
        text: str,
        user_id: str,
        model_id: str | None = None,
        session_key: str | None = None,
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        if self.state != ConnectionState.CONNECTED:
            if self.state == ConnectionState.DEGRADED:
                self._ensure_reconnecting()
            yield OrchestratorEvent(type="error", content=ORCHESTRATOR_UNAVAILABLE_ERROR)
            return
        try:
            # aclosing() here (not just around the outer generator in
            # call_orchestrator) guarantees the inner OpenClawClient.stream_response()
            # generator — which holds `finally: self._turn_registry.unregister(...)` —
            # gets a synchronous GeneratorExit when this generator is closed early,
            # instead of being abandoned for the asyncgen GC finalizer to eventually
            # close (issue #150, one layer removed from the #99/#147 fix).
            async with contextlib.aclosing(
                self._client.stream_response(
                    text=text,
                    user_id=user_id,
                    model_id=model_id,
                    session_key=session_key,
                )
            ) as inner:
                async for event in inner:
                    yield event
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[%s] stream_response error (%s)", self._name, type(e).__name__)
            logger.debug(
                "[%s] stream_response traceback (no message): %s", self._name, traceback_no_message(e)
            )
            yield OrchestratorEvent(type="error", content=ORCHESTRATOR_ERROR)

    def status(self) -> ServiceStatus:
        return self._health.status()

    def _set_state(self, state: ConnectionState) -> None:
        self._health.set_state(state)

    def _handle_disconnect(self) -> None:
        self._ensure_reconnecting()

    def trigger_reconnect(self) -> str:
        """Admin-facing entry point: force/coalesce a reconnect attempt without
        blocking on the socket handshake. Coalesces onto any reconnect already
        in flight (e.g. from the background loop after an unexpected drop)
        instead of racing it with a second concurrent connect() — returns
        that attempt's job id instead of starting a duplicate one."""
        self._reconnect_exhausted = False
        self._cancel_slow_retry()  # G-55: el trigger toma el relevo del reintento lento
        return self._ensure_reconnecting()

    def _ensure_reconnecting(self) -> str:
        # _reconnect_job_id is never "" when _reconnect_exhausted is True:
        # that flag only ever becomes True inside _reconnect_loop(), which
        # _ensure_reconnecting() itself only starts after assigning a fresh
        # _reconnect_job_id below.
        if self._reconnect_exhausted:
            return self._reconnect_job_id
        if not self._reconnect_task or self._reconnect_task.done():
            self._reconnect_job_id = str(uuid.uuid4())
            self._set_state(ConnectionState.RECONNECTING)
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        return self._reconnect_job_id

    async def _reconnect_loop(self) -> None:
        async def attempt() -> None:
            self.gateway_info = await self._client.connect()

        recovered = await reconnect_with_backoff(
            attempt,
            self._health,
            initial_backoff=self._initial_backoff,
            max_backoff=self._max_backoff,
            max_duration=self._max_duration,
            label=self._name,
        )
        if not recovered:
            self._reconnect_exhausted = True
            self._start_slow_retry()

    def _start_slow_retry(self) -> None:
        if self._slow_retry_task is None or self._slow_retry_task.done():
            self._slow_retry_task = asyncio.create_task(self._slow_retry_loop())

    def _cancel_slow_retry(self) -> None:
        task, self._slow_retry_task = self._slow_retry_task, None
        if task is not None and not task.done():
            task.cancel()

    async def _slow_retry_loop(self) -> None:
        """G-55 (#218): DEGRADED no es terminal. Un intento cada `max_backoff` s hasta que
        OpenClaw vuelva. Sin tormenta (un único intento por ventana, el estado se queda en
        DEGRADED mientras falla) y sin relanzar la ventana completa: el circuit breaker
        (#102) sigue cerrado para ping()/stream_response()/on_disconnect. Lo cancelan
        trigger_reconnect() (que toma el relevo con un intento inmediato) y close()."""
        while self._reconnect_exhausted:
            await asyncio.sleep(self._max_backoff)
            if not self._reconnect_exhausted:
                return  # un connect() directo ya recuperó el servicio
            try:
                self.gateway_info = await self._client.connect()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._health.mark_attempt_failed(describe_error(e))
                logger.warning(
                    "[%s] degraded retry %d failed: %s",
                    self._name,
                    self._reconnect_attempts,
                    describe_error(e),
                )
                continue
            self._health.mark_connected()
            self._reconnect_exhausted = False
            logger.info("[%s] recovered from DEGRADED.", self._name)
            return
