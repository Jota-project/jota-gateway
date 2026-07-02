"""Validates that the real test agent actually invokes its configured tool
and the tool's result reaches the client — not just plain LLM text."""
from tests.e2e.ws_helpers import send_turn, ws_handshake


async def test_agent_tool_use_reaches_the_client(test_client_record, e2e_agent, tool_probe_prompt):
    prompt, expected_token = tool_probe_prompt
    ws, _ready = await ws_handshake(test_client_record["client_key"], e2e_agent)
    try:
        result = await send_turn(ws, prompt)
        assert expected_token in result["text"], (
            f"el token de prueba de la tool no apareció en la respuesta: {result['text']!r}"
        )
    finally:
        await ws.close()
