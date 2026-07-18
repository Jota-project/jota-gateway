import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.models import GatewayInfo
from src.services.protocol import OrchestratorEvent
from src.services.reconnection import ConnectionState, ServiceStatus

logger = logging.getLogger(__name__)


class ReconnectingOpenClawClient:
    """Wraps OpenClawClient with exponential backoff reconnection.

    Implements OrchestratorProtocol so JotaBridge needs no changes.
    Exposes gateway_info after successful connect.
    """

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
        self.state = ConnectionState.DEGRADED
        self.gateway_info: Optional[GatewayInfo] = None
        self._connected_at: Optional[datetime] = None
        self._reconnect_attempts: int = 0
        self._last_error: Optional[str] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reconnect_job_id: Optional[str] = None
        self._reconnect_exhausted: bool = False
        self.on_state_change = None
        # Register disconnect callback so the inner client notifies us on unexpected drops.
        self._client.on_disconnect = self._handle_disconnect

    def get_name(self) -> str:
        return self._name

    async def connect(self) -> None:
        try:
            self.gateway_info = await self._client.connect()
            self._set_state(ConnectionState.CONNECTED)
            self._connected_at = datetime.now(timezone.utc)
            self._reconnect_attempts = 0
            self._last_error = None
            self._reconnect_exhausted = False
        except Exception as e:
            self._last_error = str(e)
            logger.error("[%s] initial connect failed: %s — starting retry", self._name, e)
            self._ensure_reconnecting()

    async def close(self) -> None:
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
        model_id: Optional[str] = None,
        session_key: Optional[str] = None,
    ) -> AsyncIterator[OrchestratorEvent]:
        if self.state != ConnectionState.CONNECTED:
            if self.state == ConnectionState.DEGRADED:
                self._ensure_reconnecting()
            yield OrchestratorEvent(type="error", content="orchestrator_unavailable")
            return
        try:
            async for event in self._client.stream_response(
                text=text,
                user_id=user_id,
                model_id=model_id,
                session_key=session_key,
            ):
                yield event
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[%s] stream_response error: %s", self._name, e)
            yield OrchestratorEvent(type="error", content=str(e))

    def status(self) -> ServiceStatus:
        return ServiceStatus(
            name=self._name,
            state=self.state,
            connected_at=self._connected_at,
            reconnect_attempts=self._reconnect_attempts,
            last_error=self._last_error,
        )

    def _set_state(self, state: ConnectionState) -> None:
        self.state = state
        if self.on_state_change:
            self.on_state_change(state)

    def _handle_disconnect(self) -> None:
        self._ensure_reconnecting()

    def trigger_reconnect(self) -> str:
        """Admin-facing entry point: force/coalesce a reconnect attempt without
        blocking on the socket handshake. Coalesces onto any reconnect already
        in flight (e.g. from the background loop after an unexpected drop)
        instead of racing it with a second concurrent connect() — returns
        that attempt's job id instead of starting a duplicate one."""
        self._reconnect_exhausted = False
        return self._ensure_reconnecting()

    def _ensure_reconnecting(self) -> str:
        # Always non-None here: _reconnect_exhausted only ever becomes True
        # inside _reconnect_loop(), which _ensure_reconnecting() itself only
        # ever starts after assigning a fresh _reconnect_job_id below.
        if self._reconnect_exhausted:
            return self._reconnect_job_id
        if not self._reconnect_task or self._reconnect_task.done():
            self._reconnect_job_id = str(uuid.uuid4())
            self._set_state(ConnectionState.RECONNECTING)
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        return self._reconnect_job_id

    async def _reconnect_loop(self) -> None:
        start = time.monotonic()
        backoff = self._initial_backoff
        while True:
            try:
                self.gateway_info = await self._client.connect()
                self._set_state(ConnectionState.CONNECTED)
                self._connected_at = datetime.now(timezone.utc)
                self._reconnect_attempts = 0
                logger.info("[%s] reconnected.", self._name)
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._reconnect_attempts += 1
                self._last_error = str(e)
                logger.warning(
                    "[%s] reconnect attempt %d failed: %s",
                    self._name, self._reconnect_attempts, e,
                )

            if time.monotonic() - start >= self._max_duration:
                self._set_state(ConnectionState.DEGRADED)
                self._reconnect_exhausted = True
                logger.warning("[%s] reconnect exhausted — DEGRADED.", self._name)
                return

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._max_backoff)
