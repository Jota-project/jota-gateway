"""
Integration test for #108: barge_in_enabled=False must not interrupt
an in-flight orchestrator response when a partial transcription arrives.
"""

import asyncio
import json
import threading
import time

import pytest
import websockets
from sqlmodel import Session
from starlette.testclient import TestClient

from src.core.config import settings
from src.db.models import ClientRecord
from src.main import app
from src.services.protocol import OrchestratorEvent
from tests.integration.conftest import (
    CLIENT_ID,
    CLIENT_NAME,
    VALID_KEY,
    make_mock_orchestrator,
    make_mock_registry,
)

HANDSHAKE_AUDIO = {
    "client_key": VALID_KEY,
    "input_mode": "audio",
    "output_mode": ["text"],
}

# ---------------------------------------------------------------------------
# Fake transcriber WS server: sends a final transcription on the first audio
# chunk, then a long partial (is_final=False) on the second chunk.
# ---------------------------------------------------------------------------

_FAKE_TRANSCRIBER_PORT = 19011
_fake_transcriber_started = False


def _start_fake_transcriber():
    global _fake_transcriber_started
    if _fake_transcriber_started:
        return
    _fake_transcriber_started = True

    async def handler(ws):
        raw = await ws.recv()
        msg = json.loads(raw)
        assert msg["type"] == "config"
        await ws.send(
            json.dumps(
                {
                    "type": "ready",
                    "protocol_version": 1,
                    "session_id": "test-barge-in-session",
                }
            )
        )
        chunk_count = 0
        async for chunk in ws:
            if isinstance(chunk, bytes) and len(chunk) > 0:
                chunk_count += 1
                if chunk_count == 1:
                    await ws.send(
                        json.dumps(
                            {
                                "type": "transcription",
                                "text": "hola audio",
                                "is_final": True,
                            }
                        )
                    )
                elif chunk_count == 2:
                    await ws.send(
                        json.dumps(
                            {
                                "type": "transcription",
                                "text": "esto es una interrupcion larga",
                                "is_final": False,
                            }
                        )
                    )
                    break

    loop = asyncio.new_event_loop()

    async def run():
        async with websockets.serve(handler, "localhost", _FAKE_TRANSCRIBER_PORT):
            await asyncio.Future()

    thread = threading.Thread(
        target=lambda: loop.run_until_complete(run()),
        daemon=True,
    )
    thread.start()
    time.sleep(0.15)  # esperar a que el servidor arranque


@pytest.fixture(scope="module", autouse=True)
def start_fake_transcriber():
    _start_fake_transcriber()
    old_url = settings.TRANSCRIBER_WS_URL
    settings.TRANSCRIBER_WS_URL = f"localhost:{_FAKE_TRANSCRIBER_PORT}"
    yield
    settings.TRANSCRIBER_WS_URL = old_url


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_disabled_barge_in_does_not_interrupt_in_flight_response(
    mock_services, db_engine, monkeypatch
):
    """#108: handshake with barge_in_enabled=False + a slow (long) agent response
    + a partial transcription mid-response → the response keeps streaming to
    completion, no {"type": "interrupted"} is ever sent."""
    with Session(db_engine) as s:
        s.add(
            ClientRecord(
                id=CLIENT_ID,
                name=CLIENT_NAME,
                client_key=VALID_KEY,
                is_active=True,
                barge_in_enabled=False,
                barge_in_min_chars=5,
            )
        )
        s.commit()

    mock_orch = make_mock_orchestrator()

    async def _stream(text, user_id, model_id=None, session_key=None):
        yield OrchestratorEvent(type="token", content="respuesta ")
        await asyncio.sleep(0.3)  # keeps the turn active while the partial arrives
        yield OrchestratorEvent(type="token", content="larga completa")
        yield OrchestratorEvent(type="status", content="done")

    mock_orch.stream_response = _stream
    mock_reg = make_mock_registry(mock_orch)

    from unittest.mock import AsyncMock as _AM
    from unittest.mock import MagicMock

    monkeypatch.setattr("src.main.ReconnectingOpenClawClient", lambda *a, **kw: mock_reg)
    monkeypatch.setattr("src.main.OpenClawClient", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("src.main.FrameDispatcher", lambda *a, **kw: MagicMock())
    mock_reg.connect = _AM()
    mock_reg.close = _AM()

    received_types = []

    with TestClient(app) as client, client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_AUDIO)
        ws.send_bytes(b"\x00" * 512)  # chunk 1 -> final transcription

        msg = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("type") == "transcription":
                break
        assert msg["type"] == "transcription"
        assert msg["text"] == "hola audio"

        ws.send_json({"type": "send", "text": msg["text"]})  # starts the turn

        msg = ws.receive_json()  # turn_start
        assert msg["type"] == "turn_start"

        msg = ws.receive_json()  # first token
        assert msg["type"] == "token"
        assert msg["text"] == "respuesta "

        ws.send_bytes(b"\x00" * 512)  # chunk 2 -> partial while turn is active

        # The turn completes with a {"type": "turn_end"} wire frame (see
        # bridge.py's pipe_tokens()) — the mock orchestrator's internal
        # OrchestratorEvent(type="status", content="done") is consumed by
        # call_orchestrator() and never reaches the client verbatim. Also
        # break on "interrupted": if barge-in wrongly fires, the active
        # turn is cancelled and turn_end never arrives, so waiting only
        # for turn_end would hang until the silence watchdog force-closes
        # the connection instead of failing the assertion below cleanly.
        for _ in range(10):
            msg = ws.receive_json()
            received_types.append(msg.get("type"))
            if msg.get("type") in ("turn_end", "interrupted"):
                break

    assert "interrupted" not in received_types
    assert "transcription_partial" in received_types
    assert "token" in received_types  # second token still delivered, response not cut off
