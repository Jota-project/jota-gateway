"""Hallazgo 6 de la auditoría: la session key usa el agente ya resuelto y normalizado."""

from jota_gateway.models.schemas import Handshake
from jota_gateway.services.protocol import OrchestratorEvent


async def test_session_key_uses_stripped_resolved_agent(make_bridge):
    """Hallazgo 6: `" main "` valida como `main` y debe dar la misma sesión de OpenClaw."""
    bridge, _ = make_bridge(
        handshake=Handshake(client_key="k", input_mode="text", output_mode=["text"], agent=" main ")
    )
    captured = {}

    async def s(*a, session_key=None, **k):
        captured["sk"] = session_key
        yield OrchestratorEvent(type="token", content="a")

    bridge.orchestrator.stream_response = s
    await bridge._call_orchestrator("x")

    assert captured["sk"] == "agent:main:u"
