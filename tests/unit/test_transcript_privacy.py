"""Transcripts must not reach INFO logs or the persisted pipeline events (issue #133)."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from fogar_gateway.models.schemas import ClientConfig, Handshake
from tests.fakes.bridge import blocking

_SECRET = "TOP-SECRET-PHRASE"


@pytest.fixture
async def bridge(make_bridge, mock_tracker):
    b, _ = make_bridge(
        client_id="priv-uuid",
        input_mode="audio",
        output_mode=("text", "status"),
        transcriber=MagicMock(),
        config=ClientConfig(barge_in_enabled=True, barge_in_min_chars=3),
        tracker=mock_tracker,
    )
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
    await asyncio.wait({bridge.turns.user_task}, timeout=2)

    assert not any(_SECRET in m for m in _info_messages(caplog))


async def test_barge_in_partial_is_not_logged_at_info(bridge, caplog):
    started = asyncio.Event()
    bridge.orchestrator.stream_response = blocking(started)
    await bridge.turns.start_user_turn("x")
    await asyncio.wait_for(started.wait(), 2)

    with caplog.at_level(logging.INFO):
        await bridge._on_transcription(_SECRET, False)

    assert any("Barge-in" in m for m in _info_messages(caplog))
    assert not any(_SECRET in m for m in _info_messages(caplog))
