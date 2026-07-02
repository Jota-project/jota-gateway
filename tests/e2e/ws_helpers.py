"""Real WebSocket client helpers for tests/e2e — talk to the actual
/ws/stream endpoint of the running production gateway, same wire protocol
documented in docs/client-protocol.md."""
import asyncio
import json

import websockets

from tests.e2e.conftest import GATEWAY_WS_URL


async def ws_handshake(client_key: str, agent: str, ws_url: str = GATEWAY_WS_URL, input_mode: str = "text"):
    ws = await websockets.connect(ws_url, open_timeout=10)
    await ws.send(json.dumps({
        "client_key": client_key,
        "input_mode": input_mode,
        "output_mode": ["text"],
        "agent": agent,
    }))
    raw = await asyncio.wait_for(ws.recv(), timeout=10)
    ready = json.loads(raw)
    if ready.get("type") != "ready":
        await ws.close()
        raise AssertionError(f"Handshake falló, esperaba 'ready': {ready}")
    return ws, ready


async def send_turn(ws, text: str, timeout: float = 60.0) -> dict:
    """Sends a text turn and collects frames until turn_end or error."""
    await ws.send(json.dumps({"type": "send", "text": text}))
    tokens = []
    frames = []
    turn_id = None
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        frame = json.loads(raw)
        frames.append(frame)
        if frame["type"] == "turn_start":
            turn_id = frame["turn_id"]
        elif frame["type"] == "token":
            tokens.append(frame["text"])
        elif frame["type"] in ("turn_end", "error"):
            break
    return {"turn_id": turn_id, "text": "".join(tokens), "frames": frames}


async def cancel_active_turn(ws) -> None:
    await ws.send(json.dumps({"type": "cancel"}))
