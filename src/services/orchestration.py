import contextlib
import logging
from collections.abc import Awaitable, Callable

from src.services.openclaw.models import ToolCallEvent
from src.services.pipeline_tracker import PipelineTracker
from src.services.protocol import OrchestratorProtocol

logger = logging.getLogger(__name__)


class OrchestratorError(RuntimeError):
    """The orchestrator emitted an `error` event. Its message is always a stable code
    (`orchestrator_error`, `turn_timeout`, ...), never raw exception/OpenClaw text, so
    callers may relay it. Any other RuntimeError is NOT safe to relay (#228)."""


async def call_orchestrator(
    orchestrator: OrchestratorProtocol,
    text: str,
    session_key: str,
    tracker: PipelineTracker | None = None,
    on_token: Callable[[str], Awaitable[None]] | None = None,
    on_tool_call: Callable[[ToolCallEvent], Awaitable[None]] | None = None,
) -> None:
    """Iterate orchestrator.stream_response(), record pipeline events, call on_token per token
    and on_tool_call per tool-call event.

    Raises OrchestratorError (a RuntimeError) if the orchestrator emits an error event.
    """
    if tracker:
        await tracker.record("llm_start")

    _first_token = True
    _token_count = 0

    # aclosing() guarantees stream_response()'s generator is closed on every
    # exit path, including the `raise` below — exiting an `async for` early
    # via an exception in the loop body does NOT call the generator's
    # aclose() on its own, leaving it suspended and its `finally` (which
    # unregisters the turn in TurnRegistry) deferred to whenever Python's
    # asyncgen GC finalizer gets around to it (issue #99 follow-up).
    async with contextlib.aclosing(
        orchestrator.stream_response(text=text, session_key=session_key)
    ) as stream:
        async for event in stream:
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
                raise OrchestratorError(event.content)

    if tracker:
        await tracker.record("llm_done", token_count=_token_count)
