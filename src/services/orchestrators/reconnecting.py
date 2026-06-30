import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import AsyncIterator, Optional

from src.core.config import settings
from src.services.protocol import OrchestratorEvent, OrchestratorProtocol

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
    disconnected_at: Optional[datetime]
    reconnect_attempts: int
    last_error: Optional[str]


class ReconnectingOrchestrator:
    def __init__(self, client: OrchestratorProtocol, name: str) -> None:
        self._client = client
        self._name = name
        self._state = OrchestratorState.DEGRADED
        self._connected_at: Optional[datetime] = None
        self._disconnected_at: Optional[datetime] = None
        self._reconnect_attempts: int = 0
        self._last_error: Optional[str] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reconnect_exhausted: bool = False

        if hasattr(client, "on_disconnect"):
            client.on_disconnect = self._handle_disconnect

    # ------------------------------------------------------------------
    # OrchestratorProtocol
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        await self._client.connect()
        self._state = OrchestratorState.CONNECTED
        self._connected_at = datetime.now(timezone.utc)
        self._reconnect_attempts = 0
        self._last_error = None
        self._reconnect_exhausted = False

    async def close(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        await self._client.close()

    async def ping(self) -> bool:
        if self._state != OrchestratorState.CONNECTED:
            if self._state == OrchestratorState.DEGRADED:
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
        if self._state != OrchestratorState.CONNECTED:
            if self._state == OrchestratorState.DEGRADED:
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
            logger.warning(f"[{self._name}] stream_response inner exception: {e}")
            yield OrchestratorEvent(type="error", content=str(e))

    # ------------------------------------------------------------------
    # Observability / control
    # ------------------------------------------------------------------

    def status(self) -> OrchestratorStatus:
        return OrchestratorStatus(
            name=self._name,
            state=self._state,
            connected_at=self._connected_at,
            disconnected_at=self._disconnected_at,
            reconnect_attempts=self._reconnect_attempts,
            last_error=self._last_error,
        )

    async def trigger_reconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        self._reconnect_exhausted = False
        self._state = OrchestratorState.RECONNECTING
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _handle_disconnect(self) -> None:
        self._disconnected_at = datetime.now(timezone.utc)
        self._ensure_reconnecting()

    def _ensure_reconnecting(self) -> None:
        if self._reconnect_exhausted:
            return
        if not self._reconnect_task or self._reconnect_task.done():
            self._state = OrchestratorState.RECONNECTING
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        start = time.monotonic()
        backoff = settings.ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF

        while True:
            try:
                await self._client.connect()
                self._state = OrchestratorState.CONNECTED
                self._connected_at = datetime.now(timezone.utc)
                self._reconnect_attempts = 0
                logger.info(f"Orchestrator '{self._name}' reconnected.")
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._reconnect_attempts += 1
                self._last_error = str(e)
                logger.warning(
                    f"Orchestrator '{self._name}' reconnect attempt "
                    f"{self._reconnect_attempts} failed: {e}"
                )

            elapsed = time.monotonic() - start
            if elapsed >= settings.ORCHESTRATOR_RECONNECT_MAX_DURATION:
                self._state = OrchestratorState.DEGRADED
                self._reconnect_exhausted = True
                logger.warning(
                    f"Orchestrator '{self._name}' reconnect exhausted "
                    f"after {elapsed:.0f}s — entering DEGRADED state."
                )
                return

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, settings.ORCHESTRATOR_RECONNECT_MAX_BACKOFF)
