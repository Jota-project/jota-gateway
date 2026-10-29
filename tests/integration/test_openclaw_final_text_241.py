"""Issue #241/#243: cableado de ChatTextTracker en `stream_response`.

Servidor `websockets` real (puerto 0) que emite las tramas capturadas en producción.
Los casos del tracker viven en tests/unit/test_chat_text.py.
"""

import json
import logging

import pytest

from tests.integration.test_openclaw_real_ws import (
    SK_A,
    FakeOpenClawServer,
    errors,
    make_client,
    run_turn,
)

TEXT = "SECRETREPLY"


def _msg(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}], "timestamp": 1}


def _ev(sk, **kw):
    return json.dumps({"type": "event", "event": "chat", "payload": {"sessionKey": sk, **kw}})


def _delta(sk, text, seq, replace=False):
    kw = {"state": "delta", "seq": seq, "deltaText": text, "message": _msg(text)}
    if replace:
        kw["replace"] = True
    return _ev(sk, **kw)


def _final(sk, text, seq=11):
    return _ev(sk, state="final", seq=seq, stopReason="stop", message=_msg(text))


class Scripted2026_10_5(FakeOpenClawServer):
    async def _chat_send(self, ws, rid, params):
        sk, scenario = params["sessionKey"], params["message"]
        await ws.send(self._res(rid, {"status": "started"}))
        if scenario == "captured":
            for phase, seq in (
                ("preparing_workspace", 1),
                ("preparing_context", 2),
                ("starting_model", 4),
            ):
                await ws.send(_ev(sk, state="status", phase=phase, seq=seq))
            await ws.send(_delta(sk, "1, 2, 3,", 6))
            await ws.send(_delta(sk, "", 9, replace=True))
            await ws.send(_final(sk, "1, 2, 3, 4, 5"))
        elif scenario == "multi_message":
            await ws.send(_delta(sk, "Voy a buscar. ", 1))
            await ws.send(_final(sk, f"Resultado: {TEXT}"))


@pytest.fixture
async def server():
    async with Scripted2026_10_5() as srv:
        yield srv


async def _turn(server, scenario):
    client, _ = make_client(server.port)
    await client.connect()
    try:
        return await run_turn(client, SK_A, scenario)
    finally:
        await client.close()


def _toks(events):
    return [e.content for e in events if e.type == "token"]


async def test_captured_sequence_delivers_full_text_then_done(server):
    events = await _turn(server, "captured")
    assert _toks(events) == ["1, 2, 3,", " 4, 5"]
    assert events[-1].type == "status" and events[-1].content == "done"
    assert errors(events) == []


async def test_diverging_final_emits_full_final_and_warns_without_content(server, caplog):
    caplog.set_level(logging.DEBUG)
    events = await _turn(server, "multi_message")
    assert _toks(events) == ["Voy a buscar. ", f"Resultado: {TEXT}"]
    assert events[-1].content == "done"
    assert any("diverges" in r.getMessage() for r in caplog.records)
    assert TEXT not in caplog.text
    assert "Voy a buscar" not in caplog.text
