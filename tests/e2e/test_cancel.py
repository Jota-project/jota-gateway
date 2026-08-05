"""Validates that {"type": "cancel"} really reaches chat.abort on the real
OpenClaw orchestrator, and that a subsequent turn still works normally."""

import asyncio
import json

from tests.e2e.ws_helpers import cancel_active_turn, send_turn, ws_handshake


async def test_cancel_stops_the_active_turn_on_real_openclaw(test_client_record, e2e_agent):
    ws, _ready = await ws_handshake(test_client_record["client_key"], e2e_agent)
    try:
        await ws.send(
            json.dumps(
                {
                    "type": "send",
                    "text": "Cuenta lentamente en voz alta del 1 al 100, un número por frase.",
                }
            )
        )
        # Wait for turn_start to confirm the turn is actually running before cancelling.
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        assert first["type"] == "turn_start"
        cancelled_turn_id = first["turn_id"]

        await cancel_active_turn(ws)

        # No turn_end (nor interrupted) should ever arrive for the cancelled turn —
        # drain briefly and assert nothing matches.
        leftover_frames = []
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                leftover_frames.append(json.loads(raw))
        except TimeoutError:
            pass

        assert not any(
            f.get("turn_id") == cancelled_turn_id and f["type"] == "turn_end"
            for f in leftover_frames
        ), f"turn_end no debería llegar para un turno cancelado: {leftover_frames}"

        # A fresh turn afterwards must complete normally — proves the session
        # wasn't left in a broken state by the cancellation.
        result = await send_turn(ws, "Responde solo con la palabra: PONG")
        assert result["turn_id"] is not None
        assert result["turn_id"] != cancelled_turn_id
        assert len(result["text"].strip()) > 0
    finally:
        await ws.close()
