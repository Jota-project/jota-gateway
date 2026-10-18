"""FrameDispatcher: res semantics (G-47) and null payload/data tolerance (G-03), #213."""

import pytest

from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.registry import ClientRegistry, TurnRegistry

SK = "agent:main:client-a"


def make():
    turns = TurnRegistry()
    return FrameDispatcher(turns, ClientRegistry()), turns


async def test_res_ok_false_ends_turn_with_error_frame():
    dispatcher, turns = make()
    q = turns.register("req-1", SK)
    await dispatcher.dispatch({"type": "res", "id": "req-1", "ok": False, "error": {"code": "x"}})
    kind, data = q.get_nowait()
    assert kind == "done" and data["ok"] is False


async def test_res_ok_true_with_other_status_does_not_end_turn():
    dispatcher, turns = make()
    q = turns.register("req-1", SK)
    accepted = {"type": "res", "id": "req-1", "ok": True, "payload": {"status": "accepted"}}
    await dispatcher.dispatch(accepted)
    await dispatcher.dispatch({"type": "res", "id": "req-1", "ok": True, "payload": None})
    assert q.empty()


@pytest.mark.parametrize("event", ["chat", "agent", "session.tool"])
async def test_null_payload_or_data_is_tolerated(event):
    dispatcher, _ = make()
    await dispatcher.dispatch({"type": "event", "event": event, "payload": None})
    payload = {"sessionKey": SK, "data": None}
    await dispatcher.dispatch({"type": "event", "event": event, "payload": payload})
