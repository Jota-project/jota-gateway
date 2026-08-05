"""Real WebSocket client helpers for tests/e2e — talk to the actual
/ws/stream endpoint of the running production gateway, same wire protocol
documented in docs/client-protocol.md."""

import asyncio
import json

import websockets

from tests.e2e.conftest import GATEWAY_WS_URL


async def ws_handshake(
    client_key: str, agent: str, ws_url: str = GATEWAY_WS_URL, input_mode: str = "text"
):
    ws = await websockets.connect(ws_url, open_timeout=10)
    await ws.send(
        json.dumps(
            {
                "client_key": client_key,
                "input_mode": input_mode,
                "output_mode": ["text"],
                "agent": agent,
            }
        )
    )
    raw = await asyncio.wait_for(ws.recv(), timeout=10)
    ready = json.loads(raw)
    if ready.get("type") != "ready":
        await ws.close()
        raise AssertionError(f"Handshake falló, esperaba 'ready': {ready}")
    return ws, ready


async def send_turn(ws, text: str, timeout: float = 60.0) -> dict:
    """Sends a text turn and collects frames belonging to that turn until its
    own turn_end (or a matching/fatal error) arrives.

    Agents that invoke tools (or otherwise produce multi-part replies)
    routinely emit additional turn_start/turn_end pairs unrelated to this
    specific message — confirmed live against ci-tester, see
    docs/client-protocol.md §5. Only the first turn_id observed (the turn
    triggered by this send) is tracked for `text`/stop purposes; frames
    belonging to other turn_ids are still recorded in `frames` but ignored
    otherwise, so a stray turn_end from an unrelated turn can't truncate
    collection early.
    """
    await ws.send(json.dumps({"type": "send", "text": text}))
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    tokens = []
    frames = []
    turn_id = None
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(
                f"turn_end no llegó en {timeout}s para turn_id={turn_id}: {frames}"
            )
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        frame = json.loads(raw)
        frames.append(frame)
        if frame["type"] == "turn_start" and turn_id is None:
            turn_id = frame["turn_id"]
        elif frame["type"] == "token" and frame.get("turn_id") == turn_id:
            tokens.append(frame["text"])
        elif (frame["type"] == "turn_end" and frame.get("turn_id") == turn_id) or (
            frame["type"] == "error" and (frame.get("turn_id") == turn_id or frame.get("fatal"))
        ):
            break
    return {"turn_id": turn_id, "text": "".join(tokens), "frames": frames}


async def cancel_active_turn(ws) -> None:
    await ws.send(json.dumps({"type": "cancel"}))


async def send_turn_until_tool_call(ws, text: str, timeout: float = 100.0) -> dict:
    """Sends a text turn and collects frames until a tool_call event with
    phase == "result" arrives, or error, or the timeout elapses.

    Unlike send_turn(), this does NOT assume one turn per message: agents that
    invoke tools routinely chain several turn_start/turn_end pairs for a
    single user message (tool invocation, intermediate replies, final text),
    per docs/client-protocol.md §5. Requires the client to have
    tool_calls_enabled=True (see test_client_record_with_tools fixture).
    """
    await ws.send(json.dumps({"type": "send", "text": text}))
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    frames = []
    tool_calls = []
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(f"No llegó ningún tool_call 'result' en {timeout}s: {frames}")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        frame = json.loads(raw)
        frames.append(frame)
        if frame["type"] == "tool_call":
            tool_calls.append(frame)
            if frame["phase"] == "result":
                break
        elif frame["type"] == "error":
            break
    return {"tool_calls": tool_calls, "frames": frames}
