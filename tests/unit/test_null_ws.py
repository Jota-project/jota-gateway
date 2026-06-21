from src.services.pipeline_tracker import _NullWS


async def test_null_ws_send_json_does_not_raise():
    ws = _NullWS()
    await ws.send_json({"type": "pipeline_event", "stage": "llm_start"})


async def test_null_ws_send_json_ignores_any_data():
    ws = _NullWS()
    await ws.send_json({})
    await ws.send_json({"key": "value", "nested": {"a": 1}})
