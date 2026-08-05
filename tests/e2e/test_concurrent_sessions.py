"""Validates that OpenClawClient's multiplexing keeps concurrent sessions
isolated: N different clients, same test agent, turns in parallel, each
gets back its own distinct response without cross-talk."""

import asyncio

from tests.e2e.ws_helpers import send_turn, ws_handshake


async def _run_one_session(client_key: str, agent: str, probe_word: str) -> str:
    ws, _ready = await ws_handshake(client_key, agent)
    try:
        result = await send_turn(ws, f"Responde solo con la palabra: {probe_word}")
        return result["text"]
    finally:
        await ws.close()


async def test_three_concurrent_sessions_stay_isolated(test_client_records_x3, e2e_agent):
    probe_words = ["ALPHA", "BRAVO", "CHARLIE"]
    tasks = [
        _run_one_session(record["client_key"], e2e_agent, word)
        for record, word in zip(test_client_records_x3, probe_words)
    ]
    responses = await asyncio.gather(*tasks)

    for word, response in zip(probe_words, responses):
        response_upper = response.upper()
        assert word in response_upper, (
            f"la sesión que pidió '{word}' no la recibió de vuelta: {response!r}"
        )
        other_words = [w for w in probe_words if w != word]
        leaked = [w for w in other_words if w in response_upper]
        assert not leaked, (
            f"la sesión que pidió '{word}' recibió palabras de otra sesión "
            f"({leaked}), posible cross-talk: {response!r}"
        )
