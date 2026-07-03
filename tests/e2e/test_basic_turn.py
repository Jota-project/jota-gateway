"""Validates a full text turn against the real OpenClaw test agent."""

from tests.e2e.ws_helpers import send_turn, ws_handshake


async def test_basic_text_turn_gets_a_real_response(test_client_record, e2e_agent):
    ws, ready = await ws_handshake(test_client_record["client_key"], e2e_agent)
    try:
        assert ready["agent"] == e2e_agent
        result = await send_turn(ws, "Responde solo con la palabra: PONG")
        assert result["turn_id"] is not None
        assert len(result["text"].strip()) > 0, "el agente real debería devolver texto no vacío"
        assert not any(f["type"] == "error" for f in result["frames"]), result["frames"]
    finally:
        await ws.close()
