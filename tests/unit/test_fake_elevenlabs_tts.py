"""Sanidad del upstream ElevenLabs falso (los tests del adaptador dependen de él)."""

import asyncio
import base64
import json

import pytest
import websockets

from tests.fakes.elevenlabs_tts import FakeElevenLabsTts


async def _connect(fake, key="fake-el-key"):
    return await websockets.connect(
        f"{fake.url}/v1/text-to-speech/v/stream-input?model_id=m",
        additional_headers={"xi-api-key": key},
    )


async def test_audio_for_text_then_final_on_end():
    fake = FakeElevenLabsTts()
    await fake.start()
    try:
        ws = await _connect(fake)
        await ws.send(json.dumps({"text": " "}))
        await ws.send(json.dumps({"text": "Hola que tal estas hoy.", "flush": True}))
        first = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert len(base64.b64decode(first["audio"])) == 960
        await ws.send(json.dumps({"text": ""}))

        async def drain():
            return [json.loads(m) async for m in ws]

        msgs = await asyncio.wait_for(drain(), 2)
        assert msgs[-1] == {"isFinal": True}
        assert ws.close_code == 1000
        assert fake.messages[0] == {"text": " "}
    finally:
        await fake.stop()


async def test_wrong_key_closes_1008():
    fake = FakeElevenLabsTts()
    await fake.start()
    try:
        ws = await _connect(fake, key="nope")
        with pytest.raises(websockets.ConnectionClosed):
            await asyncio.wait_for(ws.recv(), 2)
        assert ws.close_code == 1008
    finally:
        await fake.stop()


async def test_handshake_status_429():
    fake = FakeElevenLabsTts(handshake_status=429)
    await fake.start()
    try:
        with pytest.raises(websockets.InvalidStatus) as e:
            await _connect(fake)
        assert e.value.response.status_code == 429
    finally:
        await fake.stop()
