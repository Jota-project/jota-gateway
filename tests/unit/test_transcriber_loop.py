"""Tests for TranscriberClient.listen_loop callback signature change."""

import json

import pytest

from src.services.transcriber_client import TranscriberClient


@pytest.fixture
def client():
    return TranscriberClient(url="ws://test", client_id="test")


async def make_ws(*messages):
    """Async generator simulating a websocket stream."""
    for m in messages:
        yield m


async def test_listen_loop_passes_text_and_is_final_true(client):
    """Final transcription forwarded as (text, True)."""
    msg = json.dumps({"type": "transcription", "text": "hola", "is_final": True})
    client.ws = make_ws(msg)

    received = []

    async def callback(text: str, is_final: bool):
        received.append((text, is_final))

    await client.listen_loop(on_transcription_callback=callback)

    assert received == [("hola", True)]


async def test_listen_loop_passes_text_and_is_final_false(client):
    """Partial transcription forwarded as (text, False)."""
    msg = json.dumps({"type": "transcription", "text": "ho", "is_final": False})
    client.ws = make_ws(msg)

    received = []

    async def callback(text: str, is_final: bool):
        received.append((text, is_final))

    await client.listen_loop(on_transcription_callback=callback)

    assert received == [("ho", False)]


async def test_listen_loop_passes_is_final_none_as_false(client):
    """is_final=None (absent) is coerced to False."""
    msg = json.dumps({"type": "transcription", "text": "partial"})  # no is_final key
    client.ws = make_ws(msg)

    received = []

    async def callback(text: str, is_final: bool):
        received.append((text, is_final))

    await client.listen_loop(on_transcription_callback=callback)

    assert received == [("partial", False)]


async def test_listen_loop_ignores_non_transcription_messages(client):
    """Error and warning messages do not invoke the callback."""
    msgs = [
        json.dumps({"type": "error", "message": "oops"}),
        json.dumps({"type": "warning", "message": "buffer full"}),
    ]
    client.ws = make_ws(*msgs)

    received = []

    async def callback(text: str, is_final: bool):
        received.append((text, is_final))

    await client.listen_loop(on_transcription_callback=callback)

    assert received == []


async def test_listen_loop_ignores_empty_text(client):
    """Transcription with empty text does not invoke the callback."""
    msg = json.dumps({"type": "transcription", "text": "", "is_final": True})
    client.ws = make_ws(msg)

    received = []

    async def callback(text: str, is_final: bool):
        received.append((text, is_final))

    await client.listen_loop(on_transcription_callback=callback)

    assert received == []


async def test_listen_loop_returns_immediately_when_ws_is_none(client):
    """listen_loop exits cleanly if ws is not set."""
    client.ws = None

    called = []

    async def callback(text: str, is_final: bool):
        called.append(True)

    await client.listen_loop(on_transcription_callback=callback)

    assert called == []


async def test_listen_loop_survives_malformed_frames(client):
    """#131: un frame JSON válido pero con esquema inesperado no mata el bucle."""
    bad_type = json.dumps({"type": 123})
    not_an_object = json.dumps([1, 2, 3])
    good = json.dumps({"type": "transcription", "text": "hola", "is_final": True})
    client.ws = make_ws(bad_type, not_an_object, good)

    received = []

    async def callback(text: str, is_final: bool):
        received.append((text, is_final))

    await client.listen_loop(on_transcription_callback=callback)

    assert received == [("hola", True)]


async def test_listen_loop_malformed_frame_log_has_no_payload(client, caplog):
    secret = "TOP-SECRET-PHRASE"
    client.ws = make_ws(json.dumps({"type": 123, "text": secret}))

    with caplog.at_level("DEBUG"):
        await client.listen_loop(on_transcription_callback=lambda *_: None)

    assert not any(secret in r.getMessage() for r in caplog.records)


# ── Capacity-status protocol (issue #143) ───────────────────────────────────


async def test_listen_loop_status_frame_invokes_status_callback(client):
    client.ws = make_ws(
        json.dumps({"type": "status", "state": "busy", "reason": "gpu_saturated"}),
        json.dumps({"type": "status", "state": "ok", "future_field": 1}),
    )
    seen = []

    async def on_status(state, reason):
        seen.append((state, reason))

    await client.listen_loop(on_transcription_callback=lambda *_: None, on_status_callback=on_status)

    assert seen == [("busy", "gpu_saturated"), ("ok", None)]


async def test_listen_loop_status_frame_without_callback_does_not_raise(client):
    client.ws = make_ws(json.dumps({"type": "status", "state": "busy"}))

    await client.listen_loop(on_transcription_callback=lambda *_: None)


async def test_incomplete_final_is_logged_with_reason_and_still_delivered(client, caplog):
    text = "TOP-SECRET-PHRASE"
    client.ws = make_ws(
        json.dumps(
            {
                "type": "transcription",
                "text": text,
                "is_final": True,
                "complete": False,
                "reason": "gpu_saturated_timeout",
            }
        )
    )
    received, incomplete = [], []

    async def on_final(t, is_final):
        received.append((t, is_final))

    async def on_incomplete(reason):
        incomplete.append(reason)

    with caplog.at_level("INFO"):
        await client.listen_loop(
            on_transcription_callback=on_final, on_incomplete_callback=on_incomplete
        )

    assert received == [(text, True)]
    assert incomplete == ["gpu_saturated_timeout"]
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("gpu_saturated_timeout" in m for m in warnings)
    assert not any(text in r.getMessage() for r in caplog.records)


async def test_complete_final_and_partials_do_not_report_incomplete(client):
    client.ws = make_ws(
        json.dumps({"type": "transcription", "text": "a", "is_final": True, "complete": True}),
        json.dumps({"type": "transcription", "text": "b", "is_final": True}),
        json.dumps({"type": "transcription", "text": "c", "is_final": False, "complete": False}),
    )
    incomplete = []

    async def on_incomplete(reason):
        incomplete.append(reason)

    await client.listen_loop(
        on_transcription_callback=lambda *_: _noop(), on_incomplete_callback=on_incomplete
    )

    assert incomplete == []


async def _noop():
    return None
