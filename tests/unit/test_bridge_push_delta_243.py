"""Issue #243: semantica delta/replace/snapshot en el camino de push."""

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
async def test_push_first_snapshot_once_and_later_delta():
    bridge, _ = make_bridge()
    _open(bridge)
    await bridge.deliver_push({"state": "delta", "deltaText": "Hola", "message": _m("Hola")})
    await bridge.deliver_push({"state": "delta", "deltaText": " a todos"})
    await bridge.deliver_push(_final("Hola a todos"))
    assert _sent(bridge) == ["Hola", " a todos"]


@pytest.mark.asyncio
async def test_push_replace_nonempty_emits_only_missing():
    bridge, _ = make_bridge()
    _open(bridge)
    await bridge.deliver_push({"state": "delta", "deltaText": "Hola", "message": _m("Hola")})
    await bridge.deliver_push(
        {
            "state": "delta",
            "deltaText": "Hola mundo",
            "replace": True,
            "message": _m("Hola mundo"),
        }
    )
    assert _sent(bridge) == ["Hola", " mundo"]


@pytest.mark.asyncio
async def test_push_join_mid_run_emits_snapshot():
    bridge, _ = make_bridge()
    _open(bridge)
    await bridge.deliver_push({"state": "delta", "deltaText": "mundo", "message": _m("Hola mundo")})
    assert _sent(bridge) == ["Hola mundo"]
