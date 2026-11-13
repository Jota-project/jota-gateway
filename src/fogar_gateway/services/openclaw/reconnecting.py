import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator

from fogar_gateway.services.openclaw.client import (
    ORCHESTRATOR_ERROR,
    ORCHESTRATOR_UNAVAILABLE_ERROR,
    OpenClawClient,
)
from fogar_gateway.services.openclaw.models import GatewayInfo
from fogar_gateway.services.protocol import OrchestratorEvent
from fogar_gateway.services.reconnection import (
    ConnectionState,
    ServiceHealth,
    ServiceStatus,
    describe_error,
    health_attr,
    traceback_no_message,
)
from fogar_gateway.services.teardown import Teardown

logger = logging.getLogger(__name__)


def _log_task_failure(task: asyncio.Task) -> None:
    """Done-callback de las tareas de fondo (#221): su fallo se registra por tipo, también
    cuando nadie las espera (una tarea reemplazada o cancelada sin await)."""
    exc = None if task.cancelled() else task.exception()
    if exc is not None:
        logger.warning("OpenClaw: tarea de fondo falló (%s)", describe_error(exc))
        logger.debug("OpenClaw: %s", traceback_no_message(exc))


class ReconnectingOpenClawClient:
    """Wraps OpenClawClient with a single, endless reconnection loop (#272).

    One loop retries with capped exponential backoff (`initial_backoff` doubling up to
    `max_backoff`) for as long as OpenClaw is down: there is no exhaustion, no circuit
    breaker and no second slow-retry layer. While it runs the state is RECONNECTING (the
    gateway really never stops retrying). An explicit `trigger_reconnect()` cuts the
    current wait short and retries now; `close()` stops the loop for good.

    Implements OrchestratorProtocol so FogarBridge needs no changes.
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
    ) -> None:
        self._client = client
        self._name = name
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._health = ServiceHealth(name, ConnectionState.DEGRADED)
        self.gateway_info: GatewayInfo | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._wake = asyncio.Event()  # set by trigger_reconnect()/close(): ends the backoff wait
        self._closed = False  # G-52: tras close() nada vuelve a lanzar la reconexión
        # Register disconnect callback so the inner client notifies us on unexpected drops.
        self._client.on_disconnect = self._handle_disconnect

    def get_name(self) -> str:
        return self._name

    async def connect(self) -> None:
        try:
            self.gateway_info = await self._client.connect()
            self._health.mark_connected()
        except Exception as e:  # noqa: BLE001 — frontera: el fallo arranca el reintento
            # Type/stable code only (#228): the text can carry OpenClaw-provided content.
            self._last_error = describe_error(e)
            logger.error(
                "[%s] initial connect failed (%s) — starting retry", self._name, self._last_error
            )
            logger.debug("[%s] connect traceback (no message): %s", self._name, traceback_no_message(e))
            self._ensure_loop()

    async def close(self) -> None:
        self._closed = True
        self._wake.set()
        # G-52 (#221): Teardown termina el cierre aunque cancelen a quien llama y relanza
        # esa cancelación al final. Las tareas de reintento pueden estar cerrando un socket
        # a medio handshake (hasta el close_timeout de 10 s de websockets): de ahí los 12 s.
        # El cierre del cliente interno ya acota sus propios pasos (2+2+12 s).
        async with Teardown(f"[{self._name}] close") as td:
            await td.cancel(self._reconnect_task, "reconexión", timeout=12.0)
            await td.step(self._client.close(), "cierre del cliente OpenClaw")

    async def ping(self) -> bool:
        if self.state != ConnectionState.CONNECTED:
            self._ensure_loop()
            return False
        return await self._client.ping()

    async def abort(self, session_key: str) -> None:
        if self.state == ConnectionState.CONNECTED:
            await self._client.abort(session_key)

    async def stream_response(
        self,
        text: str,
        session_key: str,
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        if self.state != ConnectionState.CONNECTED:
            self._ensure_loop()
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
                self._client.stream_response(text=text, session_key=session_key)
            ) as inner:
                async for event in inner:
                    yield event
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — frontera: el turno cede orchestrator_error
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
        self._ensure_loop()

    def trigger_reconnect(self) -> None:
        """Admin-facing entry point: retry now, without blocking on the handshake. Cuts the
        current backoff wait short (and resets the backoff); if an attempt is already in
        flight it is not duplicated, and a failure is followed by an immediate new try."""
        self._ensure_loop()
        self._wake.set()

    def _ensure_loop(self) -> None:
        """Starts the loop if it is not running. A no-op while it is, and after close()."""
        if self._closed:
            return
        if not self._reconnect_task or self._reconnect_task.done():
            self._wake.clear()
            self._set_state(ConnectionState.RECONNECTING)
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
            self._reconnect_task.add_done_callback(_log_task_failure)

    async def _reconnect_loop(self) -> None:
        backoff = self._initial_backoff
        while not self._closed and self.state != ConnectionState.CONNECTED:
            self._wake.clear()
            try:
                self.gateway_info = await self._client.connect()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — frontera: un intento fallido (de cualquier tipo) se reintenta
                # Stable description only (#228): the text can carry server-provided content.
                reason = describe_error(e)
                self._health.mark_attempt_failed(reason)
                logger.warning(
                    "[%s] reconnect attempt %d failed: %s",
                    self._name,
                    self._reconnect_attempts,
                    reason,
                )
                logger.debug(
                    "[%s] reconnect traceback (no message): %s", self._name, traceback_no_message(e)
                )
            else:
                self._health.mark_connected()
                logger.info("[%s] reconnected.", self._name)
                return
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=backoff)
            except TimeoutError:
                backoff = min(backoff * 2, self._max_backoff)
            else:
                backoff = self._initial_backoff  # explicit request (or close): retry now
