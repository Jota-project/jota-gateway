from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from fogar_gateway.services.openclaw.models import ToolCallEvent
from fogar_gateway.services.turn_progress import ProgressPhase

# `status` event content: OpenClaw accepted the turn's chat.send (its ack), before any token.
ACCEPTED = "accepted"


@dataclass
class OrchestratorEvent:
    type: Literal["token", "status", "error", "tool_call", "progress"]
    content: str = ""
    tool_call: ToolCallEvent | None = None
    # `progress`: the phase, already in the gateway's vocabulary (services/turn_progress.py,
    # `chat_status_phase`), never the orchestrator's own string (#254).
    phase: ProgressPhase | None = None


@runtime_checkable
class OrchestratorProtocol(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def ping(self) -> bool: ...
    async def abort(self, session_key: str) -> None: ...
    async def request(self, method: str, params: dict, timeout: float = ...) -> dict: ...

    def stream_response(
        self,
        text: str,
        session_key: str,
    ) -> AsyncGenerator[OrchestratorEvent, None]: ...
