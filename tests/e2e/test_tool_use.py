"""Validates that the real test agent actually invokes a real tool (read) and
that the tool's start/result reach the client as tool_call WS messages — not
just plain LLM text.

Uses IDENTITY.md, a static bootstrap file that ships in every fresh OpenClaw
agent workspace (see docs.openclaw.ai — agent-workspace concept). Its content
is fixed boilerplate the model could not reproduce from memory, so a correct
'result' payload proves the file was actually read via the tool, not
hallucinated. Requires the test client to have tool_calls_enabled=True (see
test_client_record_with_tools fixture) — otherwise the gateway never forwards
tool_call messages at all (opt-in, see CLAUDE.md).
"""
from tests.e2e.ws_helpers import send_turn_until_tool_call, ws_handshake

EXPECTED_IDENTITY_SNIPPET = "This isn't just metadata. It's the start of figuring out who you are."


async def test_agent_tool_use_reaches_the_client(test_client_record_with_tools, e2e_agent):
    ws, _ready = await ws_handshake(test_client_record_with_tools["client_key"], e2e_agent)
    try:
        result = await send_turn_until_tool_call(
            ws,
            "Lee el archivo IDENTITY.md de tu workspace y dime qué pone.",
        )
        starts = [tc for tc in result["tool_calls"] if tc["phase"] == "start"]
        results = [tc for tc in result["tool_calls"] if tc["phase"] == "result"]
        assert starts, f"no llegó ningún tool_call de fase 'start': {result['frames']}"
        assert results, f"no llegó ningún tool_call de fase 'result': {result['frames']}"
        assert results[0]["name"] == "read", f"se esperaba la tool 'read': {results[0]}"
        assert EXPECTED_IDENTITY_SNIPPET in (results[0]["result"] or ""), (
            f"el resultado de la tool no contiene el contenido esperado de IDENTITY.md: {results[0]}"
        )
    finally:
        await ws.close()
