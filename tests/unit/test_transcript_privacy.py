"""Transcripts must not reach INFO logs or the persisted pipeline events (issue #133)."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry

_CLIENT = Client(id="priv-uuid", client_key="k", is_active=True)
_SECRET = "TOP-SECRET-PHRASE"


@pytest.fixture
def bridge(mock_tracker):
    b = JotaBridge(
        client=_CLIENT,
        config=ClientConfig(barge_in_enabled=True, barge_in_min_chars=3),
        client_ws=AsyncMock(),
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=mock_tracker,
        handshake=Handshake(client_key="k", input_mode="audio", output_mode=["text", "status"]),
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    b.transcriber = MagicMock()
    return b


def _info_messages(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]


async def test_final_transcription_event_keeps_only_length(bridge, caplog):
    with caplog.at_level(logging.INFO):
        await bridge._on_transcription(_SECRET, True)

    event = next(e for e in bridge.tracker.events if e.stage == "transcription_final")
    assert "text" not in event.meta
    assert event.meta == {"text_len": len(_SECRET)}
    assert not any(_SECRET in m for m in _info_messages(caplog))


async def test_send_message_is_not_logged_at_info(bridge, caplog):
    bridge.handshake = Handshake(client_key="k", input_mode="audio", output_mode=["text"])
    bridge.client_ws.receive = AsyncMock(
        side_effect=[
            {"type": "websocket.message", "text": json.dumps({"type": "send", "text": _SECRET})},
            {"type": "websocket.disconnect"},
        ]
    )

    with caplog.at_level(logging.INFO):
        await bridge._client_input_loop()
    bridge._active_turn.cancel()

    assert not any(_SECRET in m for m in _info_messages(caplog))


async def test_barge_in_partial_is_not_logged_at_info(bridge, caplog):
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    with caplog.at_level(logging.INFO):
        await bridge._on_transcription(_SECRET, False)

    assert any("Barge-in" in m for m in _info_messages(caplog))
    assert not any(_SECRET in m for m in _info_messages(caplog))
