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
