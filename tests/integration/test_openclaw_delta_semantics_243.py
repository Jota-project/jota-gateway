"""Issue #243: semantica delta/replace/snapshot con servidor `websockets` real."""

import logging

import pytest

from tests.integration.test_openclaw_final_text_241 import _ev, _final, _msg
from tests.integration.test_openclaw_real_ws import (
    SK_A,
    FakeOpenClawServer,
    make_client,
    run_turn,
)


def _d(sk, text, seq, message=None, replace=False):
    kw = {"state": "delta", "seq": seq, "deltaText": text}
    if message is not None:
        kw["message"] = _msg(message)
    if replace:
        kw["replace"] = True
    return _ev(sk, **kw)


class Scripted(FakeOpenClawServer):
    async def _chat_send(self, ws, rid, params):
        sk, sc = params["sessionKey"], params["message"]
        await ws.send(self._res(rid, {"status": "started"}))
        if sc == "captured":
            await ws.send(_d(sk, "1, 2, 3,", 6, "1, 2, 3,"))
            await ws.send(_d(sk, "", 9, "", replace=True))
            await ws.send(_final(sk, "1, 2, 3, 4, 5"))
        elif sc == "replace":
            await ws.send(_d(sk, "Hola mun", 1, "Hola mun"))
            await ws.send(_d(sk, "Hola, mundo", 2, "Hola, mundo", replace=True))
            await ws.send(_final(sk, "Hola, mundo"))
        elif sc == "first_snapshot":
            await ws.send(_d(sk, "Hola", 1, "Hola"))
            await ws.send(_final(sk, "Hola"))
        elif sc == "join_mid_run":
            await ws.send(_d(sk, "mundo", 1, "Hola mundo"))
            await ws.send(_final(sk, "Hola mundo"))
        elif sc == "later_delta":
            await ws.send(_d(sk, "Hola", 1, "Hola"))
            await ws.send(_d(sk, " a todos", 2))
            await ws.send(_final(sk, "Hola a todos"))
        elif sc == "final_corrects":
            await ws.send(_d(sk, "SECRETREPLY uno", 1, "SECRETREPLY uno"))
            await ws.send(_final(sk, "SECRETREPLY dos"))


@pytest.fixture
async def server():
    async with Scripted() as srv:
        yield srv


async def _toks(server, sc):
    client, _ = make_client(server.port)
    await client.connect()
    try:
        ev = await run_turn(client, SK_A, sc)
    finally:
        await client.close()
    assert ev[-1].content == "done"
    return [e.content for e in ev if e.type == "token"]


async def test_captured(server):
    assert await _toks(server, "captured") == ["1, 2, 3,", " 4, 5"]


async def test_replace_nonempty_not_whole_as_addition(server, caplog):
    caplog.set_level(logging.DEBUG)
    toks = await _toks(server, "replace")
    assert toks == ["Hola mun", "Hola, mundo"]
    assert "diverges" in caplog.text
    assert "mundo" not in caplog.text.split("diverges")[1]


async def test_first_snapshot_once(server):
    assert await _toks(server, "first_snapshot") == ["Hola"]


async def test_join_mid_run(server):
    assert await _toks(server, "join_mid_run") == ["Hola mundo"]


async def test_later_delta_only_addition(server):
    assert await _toks(server, "later_delta") == ["Hola", " a todos"]


async def test_final_correction_diverges(server, caplog):
    caplog.set_level(logging.DEBUG)
    toks = await _toks(server, "final_corrects")
    assert toks == ["SECRETREPLY uno", "SECRETREPLY dos"]
    assert "SECRETREPLY" not in caplog.text
