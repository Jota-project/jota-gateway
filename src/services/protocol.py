from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from src.services.openclaw.models import ToolCallEvent


@dataclass
class OrchestratorEvent:
    type: Literal["token", "status", "error", "tool_call"]
    content: str = ""
    tool_call: ToolCallEvent | None = None


@runtime_checkable
class OrchestratorProtocol(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def ping(self) -> bool: ...

    async def stream_response(
        self,
        text: str,
        user_id: str,
        model_id: str | None = None,
        session_key: str | None = None,
    ) -> AsyncIterator[OrchestratorEvent]: ...
