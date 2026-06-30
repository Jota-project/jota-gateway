import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import AsyncIterator, Optional

from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.models import GatewayInfo
from src.services.protocol import OrchestratorEvent

logger = logging.getLogger(__name__)


class OrchestratorState(Enum):
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    DEGRADED = "DEGRADED"


@dataclass
class OrchestratorStatus:
    name: str
    state: OrchestratorState
    connected_at: Optional[datetime]
    reconnect_attempts: int
    last_error: Optional[str]


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
        self.state = OrchestratorState.DEGRADED
        self.gateway_info: Optional[GatewayInfo] = None
        self._connected_at: Optional[datetime] = None
        self._reconnect_attempts: int = 0
        self._last_error: Optional[str] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        # Register disconnect callback so the inner client notifies us on unexpected drops.
        self._client.on_disconnect = self._handle_disconnect

    async def connect(self) -> None:
        try:
            self.gateway_info = await self._client.connect()
            self.state = OrchestratorState.CONNECTED
            self._connected_at = datetime.now(timezone.utc)
            self._reconnect_attempts = 0
            self._last_error = None
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
        if self.state != OrchestratorState.CONNECTED:
            if self.state == OrchestratorState.DEGRADED:
                self._ensure_reconnecting()
            return False
        return await self._client.ping()

    async def stream_response(
        self,
        text: str,
        user_id: str,
        model_id: Optional[str] = None,
        system_prompt_extra: Optional[str] = None,
        session_key: Optional[str] = None,
    ) -> AsyncIterator[OrchestratorEvent]:
        if self.state != OrchestratorState.CONNECTED:
            if self.state == OrchestratorState.DEGRADED:
                self._ensure_reconnecting()
            yield OrchestratorEvent(type="error", content="orchestrator_unavailable")
            return
        try:
            async for event in self._client.stream_response(
                text=text,
                user_id=user_id,
                model_id=model_id,
                system_prompt_extra=system_prompt_extra,
                session_key=session_key,
            ):
                yield event
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[%s] stream_response error: %s", self._name, e)
            yield OrchestratorEvent(type="error", content=str(e))

    def status(self) -> OrchestratorStatus:
        return OrchestratorStatus(
            name=self._name,
            state=self.state,
            connected_at=self._connected_at,
            reconnect_attempts=self._reconnect_attempts,
            last_error=self._last_error,
        )

    def _handle_disconnect(self) -> None:
        self._ensure_reconnecting()

    def _ensure_reconnecting(self) -> None:
        if not self._reconnect_task or self._reconnect_task.done():
            self.state = OrchestratorState.RECONNECTING
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        start = time.monotonic()
        backoff = self._initial_backoff
        while True:
            try:
                self.gateway_info = await self._client.connect()
                self.state = OrchestratorState.CONNECTED
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
                self.state = OrchestratorState.DEGRADED
                logger.warning("[%s] reconnect exhausted — DEGRADED.", self._name)
                return

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._max_backoff)
