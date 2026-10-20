"""El cierre de sesión registra contadores de uso de voz, solo números (fase 2)."""

from unittest.mock import AsyncMock

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from tests.unit.test_bridge_speech_ports import FakeStt


class UsageStt(FakeStt):
    name = "fakestt"

    def open_session(self, *, client, params, callbacks):
        s = super().open_session(client=client, params=params, callbacks=callbacks)
        s.usage = lambda: {"audio_s": 12.5, "sessions_opened": 1}
        return s


async def test_session_end_records_speech_usage_numbers_only(mock_tracker):
    tts = AsyncMock()
    tts.name = "faketts"
    tts_stream = AsyncMock()
    tts.connect = AsyncMock(return_value=tts_stream)
    bridge = JotaBridge(
        client=Client(id="c1", client_key="k", is_active=True),
        config=ClientConfig(),
        client_ws=AsyncMock(),
        orchestrator=AsyncMock(),
        tts=tts,
        tracker=mock_tracker,
        handshake=Handshake(
            client_key="k", input_mode="audio", output_mode=["audio", "text"], agent="main"
        ),
        client_registry=ClientRegistry(),
        default_agent="main",
        stt=UsageStt(),
    )
    await bridge.connect_internal_services()
    bridge._push_turn_open = True
    bridge._push_tts = tts_stream
    await bridge.deliver_push({"state": "delta", "deltaText": "hola secreto"})
    await bridge.close_all()

    events = [e for e in mock_tracker.events if e.stage == "speech_usage"]
    assert len(events) == 1
    meta = events[0].meta
    assert meta["stt_provider"] == "fakestt"
    assert meta["tts_provider"] == "faketts"
    assert meta["tts_chars"] == len("hola secreto")
    assert meta["audio_s"] == 12.5
    assert "hola secreto" not in repr(meta)
    assert all(isinstance(v, (int, float, str)) for v in meta.values())
