"""Hallazgo 6 de la auditoría: la session key usa el agente ya resuelto y normalizado."""

from src.models.schemas import Handshake
from src.services.protocol import OrchestratorEvent
from tests.unit.test_bridge_m6c_turns import _bridge


async def test_session_key_uses_stripped_resolved_agent():
    """Hallazgo 6: `" main "` valida como `main` y debe dar la misma sesión de OpenClaw."""
    bridge, _ = _bridge()
    bridge.handshake = Handshake(
        client_key="k", input_mode="text", output_mode=["text"], agent=" main "
    )
    captured = {}

    async def s(*a, session_key=None, **k):
        captured["sk"] = session_key
        yield OrchestratorEvent(type="token", content="a")

    bridge.orchestrator.stream_response = s
    await bridge._call_orchestrator("x")

    assert captured["sk"] == "agent:main:u"
