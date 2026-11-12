from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from fogar_gateway.services.openclaw.models import ToolCallEvent


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
    async def abort(self, session_key: str) -> None: ...

    def stream_response(
        self,
        text: str,
        session_key: str,
    ) -> AsyncGenerator[OrchestratorEvent, None]: ...
