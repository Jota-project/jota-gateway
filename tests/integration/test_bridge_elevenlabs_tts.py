"""Un turno de texto real del bridge con el adaptador ElevenLabs contra el upstream falso."""

from pydantic import SecretStr

from src.core.config import settings
from src.models.schemas import ClientConfig
from src.services.speech.elevenlabs.tts import ElevenLabsTtsProvider
from tests.fakes.elevenlabs_tts import FakeElevenLabsTts
from tests.unit.test_bridge_m6c_turns import _bridge, _tokens


async def test_bridge_turn_with_elevenlabs_tts_streams_audio_frames():
    fake = FakeElevenLabsTts(api_key="fake-el-key")
    await fake.start()
    try:
        provider = ElevenLabsTtsProvider(
            settings.model_copy(
                update={
                    "ELEVENLABS_API_KEY": SecretStr("fake-el-key"),
                    "ELEVENLABS_BASE_URL": fake.url,
                    "ELEVENLABS_TTS_DEFAULT_VOICE": "gc61tTk93h3LBXPxew2V",
                    "TTS_VOICE_MAP": {},
                }
            )
        )
        bridge, ws = _bridge(
            output_mode=("audio", "text"),
            config=ClientConfig(tts_voice="af_heart", tts_speed=1.0),
            tts=provider,
        )
        bridge.orchestrator.stream_response = _tokens("Hola, ", "esto es una prueba. ", "Adiós.")
        await bridge._call_orchestrator("x")

        frames = [m for m in ws.sent if isinstance(m, bytes)]
        assert frames, "el cliente no recibió audio"
        assert all(f[0] == 0xA1 and len(f) > 3 and (len(f) - 3) % 2 == 0 for f in frames)
        assert len(ws.of("turn_end")) == 1
        # af_heart (voz Kokoro) cayó en la voz por defecto antes de conectar
        assert fake.connections[0]["path"].split("/")[3] == "gc61tTk93h3LBXPxew2V"
    finally:
        await fake.stop()
