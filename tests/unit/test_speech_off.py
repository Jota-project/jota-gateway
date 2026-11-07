"""Proveedor de voz `off`: la sesión sigue solo en texto, sin tocar la red."""

from unittest.mock import AsyncMock

from jota_gateway.core.config import Settings
from jota_gateway.core.startup_checks import check_speech_settings
from jota_gateway.models.schemas import Client
from jota_gateway.services.reconnection import ConnectionState, ServiceStatus
from jota_gateway.services.speech.factory import build_stt_provider, build_tts_provider
from jota_gateway.services.speech.off import OffSttProvider, OffTtsProvider
from jota_gateway.services.speech.ports import SttCallbacks, SttParams


def _disabled(name: str) -> ServiceStatus:
    return ServiceStatus(
        name=name,
        state=ConnectionState.DEGRADED,
        connected_at=None,
        reconnect_attempts=0,
        last_error="disabled",
    )


async def test_off_providers_never_touch_network():
    stt, tts = OffSttProvider(), OffTtsProvider()
    for p in (stt, tts):
        await p.start()
        assert p.name == "off" and p.state == ConnectionState.DEGRADED
        assert await p.probe() is False
        await p.aclose()
    assert await tts.connect(voice="af_heart", speed=1.0, client_id="c1") is None
    assert (stt.status(), tts.status()) == (_disabled("transcriber"), _disabled("tts"))

    # La sesión STT se comporta como un transcriber caído que ya no reintenta.
    callbacks = SttCallbacks(AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock())
    session = stt.open_session(
        client=Client(id="c1", client_key="k", is_active=True),
        params=SttParams("es", 0.4),
        callbacks=callbacks,
    )
    assert session.state == ConnectionState.DEGRADED and session.last_transcript_at is None
    await session.start()
    await session.send_audio(b"\x00" * 1280)
    await session.send_end()
    await session.run()  # termina en seguida
    await session.close()
    await session.close()
    for cb in (callbacks.on_transcription, callbacks.on_warning, callbacks.on_status):
        cb.assert_not_awaited()


def test_factory_builds_off():
    s = Settings(_env_file=None, STT_PROVIDER="off", TTS_PROVIDER="off")
    assert build_stt_provider(s).name == "off"
    assert build_tts_provider(s).name == "off"
    check_speech_settings(s, stt_provider="off", tts_provider="off")  # nada que exigir
