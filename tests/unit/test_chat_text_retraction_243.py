"""Issue #243: retractacion a un prefijo estricto de lo emitido -> no se emite nada."""

import logging

import pytest

from src.services.openclaw.chat_text import ChatTextTracker, missing_tail
from tests.unit.test_bridge_push import make_bridge


def _m(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _final(text):
    return {"state": "final", "message": _m(text)}


def test_final_retracted_to_prefix_emits_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    t = ChatTextTracker()
    assert t.feed({"state": "delta", "deltaText": "SECRETO, mundo. Adios"}) == "SECRETO, mundo. Adios"
    assert t.feed(_final("SECRETO, mundo.")) == ""
    assert "retracted" in caplog.text
    assert "diverges" not in caplog.text
    assert "SECRETO" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_replace_retracted_to_prefix_emits_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    t = ChatTextTracker()
    t.feed({"state": "delta", "deltaText": "SECRETO, mundo. Adios", "message": _m("SECRETO, mundo. Adios")})
    out = t.feed(
        {"state": "delta", "deltaText": "SECRETO, mundo.", "replace": True,
         "message": _m("SECRETO, mundo.")}
    )
    assert out == ""
    assert "diverges" not in caplog.text
    assert "SECRETO" not in caplog.text


def test_growth_after_retraction_emits_only_new_tail():
    t = ChatTextTracker()
    t.feed({"state": "delta", "deltaText": "Hola mundo"})
    assert t.feed({"state": "delta", "deltaText": "Hola", "replace": True}) == ""
    assert t.feed({"state": "delta", "deltaText": " mundo!"}) == "!"


def test_real_divergence_still_emits_full_with_warning(caplog):
    caplog.set_level(logging.DEBUG)
    assert missing_tail("abc", "xyz") == "xyz"
    assert "diverges" in caplog.text


@pytest.mark.asyncio
async def test_push_final_retracted_to_prefix_emits_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    bridge, _ = make_bridge()
    bridge._push_turn_open = True
    bridge._push_turn_id = "t-1"
    await bridge.deliver_push({"state": "delta", "deltaText": "SECRETO, mundo. Adios"})
    await bridge.deliver_push(_final("SECRETO, mundo."))
    sent = [c.args[0]["text"] for c in bridge.client_ws.send_json.call_args_list]
    assert sent == ["SECRETO, mundo. Adios"]
    assert "diverges" not in caplog.text
    assert "SECRETO" not in caplog.text
