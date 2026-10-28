"""Issue #241/#243: cableado de ChatTextTracker en el camino de push (`deliver_push`).

Los casos del tracker viven en tests/unit/test_chat_text.py.
"""

import logging

import pytest

from tests.unit.test_bridge_push import make_bridge


def _m(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _final(text):
    return {"state": "final", "message": _m(text)}


def _sent(bridge):
    return [c.args[0]["text"] for c in bridge.client_ws.send_json.call_args_list]


def _open(bridge):
    bridge._push_turn_open = True
    bridge._push_turn_id = "t-1"


@pytest.mark.asyncio
async def test_push_captured_sequence_with_snapshots():
    bridge, _ = make_bridge()
    _open(bridge)
    await bridge.deliver_push({"state": "delta", "deltaText": "1, 2, 3,", "message": _m("1, 2, 3,")})
    await bridge.deliver_push(
        {"state": "delta", "deltaText": "", "replace": True, "message": _m("")}
    )
    await bridge.deliver_push(_final("1, 2, 3, 4, 5"))
    assert _sent(bridge) == ["1, 2, 3,", " 4, 5"]


@pytest.mark.asyncio
async def test_push_final_retracted_to_prefix_emits_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    bridge, _ = make_bridge()
    _open(bridge)
    await bridge.deliver_push({"state": "delta", "deltaText": "SECRETO, mundo. Adios"})
    await bridge.deliver_push(_final("SECRETO, mundo."))
    assert _sent(bridge) == ["SECRETO, mundo. Adios"]
    assert "diverges" not in caplog.text
    assert "SECRETO" not in caplog.text
