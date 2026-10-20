import pytest

from tests.unit.test_bridge_push import make_bridge


def _final(text):
    return {
        "state": "final",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _sent(bridge):
    return [c.args[0]["text"] for c in bridge.client_ws.send_json.call_args_list]


def _open(bridge):
    bridge._push_turn_open = True
    bridge._push_turn_id = "t-1"


@pytest.mark.asyncio
async def test_push_final_emits_missing_tail():
    bridge, _ = make_bridge()
    _open(bridge)
    await bridge.deliver_push({"state": "delta", "deltaText": "1, 2, 3,"})
    await bridge.deliver_push({"state": "delta", "deltaText": "", "replace": True})
    await bridge.deliver_push(_final("1, 2, 3, 4, 5"))
    assert _sent(bridge) == ["1, 2, 3,", " 4, 5"]


@pytest.mark.asyncio
async def test_push_final_without_deltas_then_next_response_no_duplicate():
    bridge, _ = make_bridge()
    _open(bridge)
    await bridge.deliver_push(_final("completo"))
    await bridge.deliver_push({"state": "delta", "deltaText": "otra"})
    await bridge.deliver_push(_final("otra"))
    assert _sent(bridge) == ["completo", "otra"]


@pytest.mark.asyncio
async def test_push_final_diverging_emits_full_final():
    bridge, _ = make_bridge()
    _open(bridge)
    await bridge.deliver_push({"state": "delta", "deltaText": "Voy a buscar. "})
    await bridge.deliver_push(_final("Resultado: 5"))
    assert _sent(bridge) == ["Voy a buscar. ", "Resultado: 5"]


@pytest.mark.asyncio
async def test_push_final_trimmed_space_emits_suffix():
    bridge, _ = make_bridge()
    _open(bridge)
    await bridge.deliver_push({"state": "delta", "deltaText": " Hola"})
    await bridge.deliver_push(_final("Hola mundo"))
    assert _sent(bridge) == [" Hola", " mundo"]


@pytest.mark.asyncio
async def test_push_final_without_message_does_not_break():
    bridge, _ = make_bridge()
    _open(bridge)
    await bridge.deliver_push({"state": "final"})
    assert _sent(bridge) == []
