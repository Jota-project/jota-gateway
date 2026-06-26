from dataclasses import dataclass
from typing import Literal, AsyncIterator, Optional, Protocol, runtime_checkable


@dataclass
class OrchestratorEvent:
    type: Literal["token", "status", "error"]
    content: str = ""


@runtime_checkable
class OrchestratorProtocol(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def ping(self) -> bool: ...

    async def stream_response(
        self,
        text: str,
        user_id: str,
        model_id: Optional[str] = None,
        system_prompt_extra: Optional[str] = None,
        session_key: Optional[str] = None,
    ) -> AsyncIterator[OrchestratorEvent]: ...
