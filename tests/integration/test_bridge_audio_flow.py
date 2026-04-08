"""
Tests para JotaBridge en modo audio.

input_mode=audio: el bridge conecta al transcriber WS, manda PCM,
recibe transcripción is_final, llama al orchestrator, devuelve tokens al cliente.
"""
import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import websockets

from src.main import app
from src.core.config import settings
from starlette.testclient import TestClient
from tests.integration.conftest import VALID_KEY, SESSION_RESPONSE

HANDSHAKE_AUDIO = {
    "client_key": VALID_KEY,
    "input_mode": "audio",
    "output_mode": ["text"],
}

# ---------------------------------------------------------------------------
# Fake transcriber WS server
# ---------------------------------------------------------------------------

_FAKE_TRANSCRIBER_PORT = 19009
_fake_transcriber_started = False


def _start_fake_transcriber():
    """Arranca fake transcriber en puerto 19009 en un hilo daemon."""
    global _fake_transcriber_started
    if _fake_transcriber_started:
        return
    _fake_transcriber_started = True

    async def handler(ws):
        # Handshake: recibe config, responde ready
        raw = await ws.recv()
        msg = json.loads(raw)
        assert msg["type"] == "config"
        await ws.send(json.dumps({
            "type": "ready",
            "protocol_version": 1,
            "session_id": "test-audio-session",
        }))
        # Espera audio, responde con transcripción final
        async for chunk in ws:
            if isinstance(chunk, bytes) and len(chunk) > 0:
                await ws.send(json.dumps({
                    "type": "transcription",
                    "text": "hola desde audio",
                    "is_final": True,
                }))
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
    """Arranca el fake transcriber una sola vez por módulo."""
    _start_fake_transcriber()
    old_url = settings.TRANSCRIBER_WS_URL
    settings.TRANSCRIBER_WS_URL = f"localhost:{_FAKE_TRANSCRIBER_PORT}"
    yield
    settings.TRANSCRIBER_WS_URL = old_url


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_audio_chunk_transcribed_and_forwarded_to_orchestrator(mock_services):
    """PCM → transcriber fake emite is_final → cliente recibe transcripción
    → cliente envía {"type":"send"} → orchestrator llamado con el texto."""
    called_with_text = {}

    def capture(req):
        called_with_text["text"] = json.loads(req.content).get("text")
        return httpx.Response(
            200,
            content=b'{"type":"token","content":"ok"}\n',
            headers={"content-type": "application/x-ndjson"},
        )

    mock_services.post("http://localhost:8000/api/quick").mock(side_effect=capture)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_json(HANDSHAKE_AUDIO)
            ws.send_bytes(b"\x00" * 512)  # PCM fake
            # Esperar transcripción final (nuevo protocolo: gateway no auto-despacha)
            for _ in range(10):
                msg = ws.receive_json()
                if msg.get("type") == "transcription":
                    break
            assert msg["type"] == "transcription"
            assert msg["text"] == "hola desde audio"
            # Cliente confirma y envía al orquestador
            ws.send_json({"type": "send", "text": msg["text"]})
            # Esperar token del orquestador
            for _ in range(10):
                msg = ws.receive_json()
                if msg.get("type") == "token":
                    break
            assert msg["type"] == "token"

    assert called_with_text.get("text") == "hola desde audio"


def test_transcriber_connect_uses_stt_language_from_config(mock_services):
    """stt_language de ClientConfig se pasa a TranscriberClient.connect()."""
    session_fr = {
        **SESSION_RESPONSE,
        "config": {**SESSION_RESPONSE["config"], "stt_language": "fr"},
    }
    mock_services.get("http://localhost:8001/auth/session").mock(
        return_value=httpx.Response(200, json=session_fr)
    )

    connect_calls = []
    original_connect = __import__(
        "src.services.transcriber_client", fromlist=["TranscriberClient"]
    ).TranscriberClient.connect

    async def spy_connect(self, language="es", token="", vad_thold=0.0):
        connect_calls.append({"language": language, "token": token})
        await original_connect(self, language=language, token=token, vad_thold=vad_thold)

    with patch("src.services.transcriber_client.TranscriberClient.connect", spy_connect):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/stream") as ws:
                ws.send_json(HANDSHAKE_AUDIO)
                ws.send_bytes(b"\x00" * 512)
                try:
                    ws.receive_json()
                except Exception:
                    pass

    assert connect_calls, "TranscriberClient.connect nunca fue llamado"
    assert connect_calls[0]["language"] == "fr"


def test_tts_connect_uses_voice_and_speed_from_config(mock_services):
    """tts_voice y tts_speed de ClientConfig se pasan a TTSClient.connect()."""
    session = {
        **SESSION_RESPONSE,
        "config": {**SESSION_RESPONSE["config"], "tts_voice": "bf_emma", "tts_speed": 1.2},
    }
    mock_services.get("http://localhost:8001/auth/session").mock(
        return_value=httpx.Response(200, json=session)
    )

    connect_calls = []

    async def spy_tts_connect(self, voice=None, speed=None):
        connect_calls.append({"voice": voice, "speed": speed})
        # Stub: does not call original — verifies args at bridge level only.
        # Wire format (auth message contents) is covered by TTSClient unit tests.
        self.ws = AsyncMock()
        self.ws.recv = AsyncMock(return_value=json.dumps({"type": "auth_ok"}))

    with patch("src.services.tts_client.TTSClient.connect", spy_tts_connect):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/stream") as ws:
                # output_mode=["audio"] para que el bridge instancie TTSClient
                ws.send_json({
                    "client_key": VALID_KEY,
                    "input_mode": "text",
                    "output_mode": ["audio", "text"],
                })
                ws.send_text("test")
                try:
                    ws.receive_json()
                except Exception:
                    pass

    assert connect_calls, "TTSClient.connect nunca fue llamado"
    assert connect_calls[0]["voice"] == "bf_emma"
    assert connect_calls[0]["speed"] == 1.2
