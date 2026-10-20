"""Sanidad del upstream ElevenLabs STT falso (los tests del adaptador dependen de él)."""

import asyncio
import base64
import json

import websockets

from tests.fakes.elevenlabs_stt import FakeElevenLabsStt

PATH = "/v1/speech-to-text/realtime?model_id=scribe_v2_realtime&language_code=es"


async def _connect(fake, key="fake-el-key"):
    return await websockets.connect(
        f"{fake.url}{PATH}", additional_headers={"xi-api-key": key}
    )


def _chunk(nbytes: int, commit: bool = False) -> str:
    return json.dumps(
        {
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(b"\x01\x00" * (nbytes // 2)).decode(),
            "commit": commit,
            "sample_rate": 16000,
        }
    )


async def _recv(ws):
    return json.loads(await asyncio.wait_for(ws.recv(), 2))


async def test_session_started_then_scripted_partials_and_commit():
    fake = FakeElevenLabsStt(on_audio=[("hola", False)], on_end=[("hola mundo", True)])
    await fake.start()
    try:
        ws = await _connect(fake)
        assert (await _recv(ws))["message_type"] == "session_started"
        await ws.send(_chunk(3200))
        assert await _recv(ws) == {"message_type": "partial_transcript", "text": "hola"}
        await ws.send(_chunk(3200, commit=True))
        assert await _recv(ws) == {"message_type": "committed_transcript", "text": "hola mundo"}
        assert fake.audio_bytes == 6400
        assert fake.commits == 1
        assert fake.connections[0]["query"]["language_code"] == "es"
        assert fake.invalid_audio == []
    finally:
        await fake.stop()


async def test_bad_key_closes_1008():
    fake = FakeElevenLabsStt()
    await fake.start()
    try:
        ws = await _connect(fake, key="wrong")
        try:
            await asyncio.wait_for(ws.recv(), 2)
        except websockets.ConnectionClosed:
            pass
        assert ws.close_code == 1008
        assert ws.close_reason == "Invalid API key"
    finally:
        await fake.stop()


async def test_error_mode_and_idle_close_and_odd_audio_flagged():
    fake = FakeElevenLabsStt(error_type="rate_limited", idle_close_s=0.2)
    await fake.start()
    try:
        ws = await _connect(fake)
        await _recv(ws)
        await ws.send(
            json.dumps(
                {"message_type": "input_audio_chunk", "audio_base_64": "AQ==", "commit": False}
            )
        )
        assert (await _recv(ws))["message_type"] == "rate_limited"
        try:
            await asyncio.wait_for(ws.recv(), 2)
        except websockets.ConnectionClosed:
            pass
        assert ws.close_code == 1000
        assert len(fake.invalid_audio) == 1
    finally:
        await fake.stop()
