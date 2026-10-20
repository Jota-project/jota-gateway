"""Issue #241: OpenClaw 2026.10.5 manda el texto completo solo en `chat` `final`.

Servidor `websockets` real (puerto 0) que emite las tramas capturadas en producción.
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
    tokens,
)

TEXT = "SECRETREPLY"


def _msg(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}], "timestamp": 1}


def _ev(sk, **kw):
    return json.dumps({"type": "event", "event": "chat", "payload": {"sessionKey": sk, **kw}})


def _status(sk, phase, seq):
    return _ev(sk, state="status", phase=phase, seq=seq)


def _delta(sk, text, seq, replace=False, snapshot=True):
    # Per spec (#243) only the first frame of a run carries `message`.
    kw = {"state": "delta", "seq": seq, "deltaText": text}
    if snapshot:
        kw["message"] = _msg(text)
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
                await ws.send(_status(sk, phase, seq))
            await ws.send(_delta(sk, "1, 2, 3,", 6))
            await ws.send(_delta(sk, "", 9, replace=True))
            await ws.send(_final(sk, "1, 2, 3, 4, 5"))
        elif scenario == "final_only":
            await ws.send(_status(sk, "starting_model", 1))
            await ws.send(_final(sk, "Hola completo"))
        elif scenario == "covered":
            await ws.send(_delta(sk, "Hola ", 1))
            await ws.send(_delta(sk, "mundo", 2, snapshot=False))
            await ws.send(_final(sk, "Hola mundo"))
        elif scenario == "diverge":
            await ws.send(_delta(sk, "Hola ", 1))
            await ws.send(_final(sk, f"Adios {TEXT}"))
        elif scenario == "multi_message":
            await ws.send(_delta(sk, "Voy a buscar. ", 1))
            await ws.send(_final(sk, f"Resultado: {TEXT}"))
        elif scenario == "trimmed":
            await ws.send(_delta(sk, " Hola", 1))
            await ws.send(_final(sk, "Hola mundo"))
        elif scenario == "xyz":
            await ws.send(_delta(sk, "abc", 1))
            await ws.send(_final(sk, "xyz"))
        elif scenario == "no_message":
            await ws.send(_delta(sk, "Hola", 1))
            await ws.send(_ev(sk, state="final", seq=2))
        elif scenario == "no_text_parts":
            await ws.send(_delta(sk, "Hola", 1))
            await ws.send(
                _ev(sk, state="final", message={"role": "assistant", "content": [{"type": "image"}]})
            )
        elif scenario == "string_content":
            await ws.send(
                _ev(sk, state="final", message={"role": "assistant", "content": "texto plano"})
            )


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


async def test_final_without_any_delta_emits_full_text(server):
    events = await _turn(server, "final_only")
    assert _toks(events) == ["Hola completo"]
    assert events[-1].content == "done"


async def test_final_equal_to_streamed_text_does_not_duplicate(server):
    events = await _turn(server, "covered")
    assert tokens(events) == "Hola mundo"


async def test_diverging_final_emits_full_final_and_warns_without_content(server, caplog):
    caplog.set_level(logging.DEBUG)
    events = await _turn(server, "multi_message")
    assert _toks(events) == ["Voy a buscar. ", f"Resultado: {TEXT}"]
    assert events[-1].content == "done"
    assert any("diverges" in r.getMessage() for r in caplog.records)
    assert TEXT not in caplog.text
    assert "Voy a buscar" not in caplog.text


async def test_final_with_trimmed_leading_space_emits_only_suffix(server):
    events = await _turn(server, "trimmed")
    assert _toks(events) == [" Hola", " mundo"]


async def test_completely_different_final_is_emitted_in_full(server):
    events = await _turn(server, "xyz")
    assert _toks(events) == ["abc", "xyz"]


@pytest.mark.parametrize("scenario", ["no_message", "no_text_parts"])
async def test_final_without_usable_message_does_not_break(server, scenario):
    events = await _turn(server, scenario)
    assert tokens(events) == "Hola"
    assert events[-1].content == "done"


async def test_string_content_is_accepted(server):
    events = await _turn(server, "string_content")
    assert tokens(events) == "texto plano"


async def test_rest_and_ws_style_join_gets_full_text(server):
    """/v1/chat/completions y el WS unen los mismos eventos `token` del stream."""
    events = await _turn(server, "captured")
    assert "".join(e.content for e in events if e.type == "token") == "1, 2, 3, 4, 5"
