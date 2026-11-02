"""/v1/chat/completions: cancelación y estado de sesión (#221).

Se llama al handler directamente para controlar el cierre del stream SSE.
"""

import asyncio
from types import SimpleNamespace

from jota_gateway.api.openai_routes import HACaller, _ChatCompletionRequest, chat_completions
from jota_gateway.services.protocol import OrchestratorEvent
from jota_gateway.services.session_registry import SessionRegistry


class _Orchestrator:
    gateway_info = None

    def __init__(self, close_delay: float = 0.0) -> None:
        self.close_delay = close_delay

    async def stream_response(self, text, session_key=None):
        try:
            yield OrchestratorEvent(type="token", content="hola")
            await asyncio.Event().wait()  # el turno no termina nunca
        finally:
            await asyncio.sleep(self.close_delay)  # cierre lento del stream


def _request(orchestrator) -> tuple[SimpleNamespace, SessionRegistry]:
    registry = SessionRegistry()
    state = SimpleNamespace(openclaw=orchestrator, session_registry=registry)
    return SimpleNamespace(app=SimpleNamespace(state=state)), registry


def _body(stream: bool) -> _ChatCompletionRequest:
    return _ChatCompletionRequest(messages=[{"role": "user", "content": "hola"}], stream=stream)


async def test_cancelled_stream_closes_session_as_error():
    """G-26: un stream cortado (cliente que se va) no es un turno completado."""
    request, registry = _request(_Orchestrator())
    resp = await chat_completions(request, _body(True), HACaller(None, None))
    gen = resp.body_iterator
    await gen.__anext__()
    await gen.aclose()

    [session] = registry.get_all()
    assert session.status == "error"


async def test_cancelled_non_stream_closes_session_as_error():
    request, registry = _request(_Orchestrator())
    task = asyncio.create_task(chat_completions(request, _body(False), HACaller(None, None)))
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    [session] = registry.get_all()
    assert session.status == "error"


async def test_stream_close_does_not_swallow_caller_cancellation():
    """Si cancelan a quien cierra el stream mientras espera al turno, la cancelación sigue."""
    request, _ = _request(_Orchestrator(close_delay=0.2))
    resp = await chat_completions(request, _body(True), HACaller(None, None))
    gen = resp.body_iterator
    await gen.__anext__()
    closer = asyncio.create_task(gen.aclose())
    await asyncio.sleep(0.05)
    closer.cancel()
    await asyncio.gather(closer, return_exceptions=True)
    assert closer.cancelled()
    await asyncio.sleep(0.25)  # deja terminar el turno
