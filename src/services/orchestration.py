import logging
from typing import Awaitable, Callable, Optional

from src.services.protocol import OrchestratorProtocol
from src.services.openclaw.models import ToolCallEvent
from src.services.pipeline_tracker import PipelineTracker

logger = logging.getLogger(__name__)


async def call_orchestrator(
    orchestrator: OrchestratorProtocol,
    text: str,
    session_key: str,
    user_id: str,
    model_id: Optional[str] = None,
    tracker: Optional[PipelineTracker] = None,
    on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    on_tool_call: Optional[Callable[[ToolCallEvent], Awaitable[None]]] = None,
) -> None:
    """Iterate orchestrator.stream_response(), record pipeline events, call on_token per token
    and on_tool_call per tool-call event.

    Raises RuntimeError if the orchestrator emits an error event.
    """
    if tracker:
        await tracker.record("llm_start")

    _first_token = True
    _token_count = 0

    async for event in orchestrator.stream_response(
        text=text,
        user_id=user_id,
        model_id=model_id,
        session_key=session_key,
    ):
        if event.type == "token":
            if _first_token:
                if tracker:
                    await tracker.record("llm_first_token")
                _first_token = False
            _token_count += 1
            if on_token:
                await on_token(event.content)

        elif event.type == "tool_call":
            if on_tool_call and event.tool_call is not None:
                await on_tool_call(event.tool_call)

        elif event.type == "error":
            raise RuntimeError(event.content)

    if tracker:
        await tracker.record("llm_done", token_count=_token_count)
