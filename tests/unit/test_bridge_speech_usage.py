"""El cierre de sesión registra contadores de uso de voz, solo números (fase 2)."""

from unittest.mock import AsyncMock

import pytest

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


def _mk_bridge(mock_tracker, tts, stt=None, input_mode="audio", speech=None):
    return JotaBridge(
        client=Client(id="c1", client_key="k", is_active=True),
        config=ClientConfig(),
        client_ws=AsyncMock(),
        orchestrator=AsyncMock(),
        tts=tts,
        tracker=mock_tracker,
        handshake=Handshake(
            client_key="k", input_mode=input_mode, output_mode=["audio", "text"], agent="main"
        ),
        client_registry=ClientRegistry(),
        default_agent="main",
        stt=stt or UsageStt(),
        speech=speech,
    )


def _named_tts(name):
    tts = AsyncMock()
    tts.name = name
    stream = AsyncMock()

    async def _no_audio():
        return
        yield b""

    stream.get_audio_stream = _no_audio
    tts.connect = AsyncMock(return_value=stream)
    return tts


async def test_tts_chars_split_per_provider_after_mid_session_swap(mock_tracker):
    class Holder:
        def __init__(self, stt, tts):
            self.stt, self.tts = stt, tts

    a, b = _named_tts("prov_a"), _named_tts("prov_b")
    holder = Holder(UsageStt(), a)
    bridge = _mk_bridge(mock_tracker, a, speech=holder)
    await bridge.connect_internal_services()
    for tts, text in ((a, "hola"), (b, "adios amigos")):
        holder.tts = tts
        # Un turno de push abre su TTS con el proveedor vigente en ese momento.
        await bridge.on_push_turn_start("agent:main:c1")
        await bridge.deliver_push({"state": "delta", "deltaText": text})
        await bridge.on_push_turn_end("agent:main:c1")
    await bridge.close_all()
    meta = next(e for e in mock_tracker.events if e.stage == "speech_usage").meta
    assert meta["tts_chars_prov_a"] == 4
    assert meta["tts_chars_prov_b"] == 12
    assert meta["tts_chars"] == 16


async def test_text_only_session_has_no_stt_provider(mock_tracker):
    bridge = _mk_bridge(mock_tracker, _named_tts("faketts"), input_mode="text")
    await bridge.connect_internal_services()
    await bridge.close_all()
    meta = next(e for e in mock_tracker.events if e.stage == "speech_usage").meta
    assert "stt_provider" not in meta
    assert meta["tts_chars"] == 0


async def test_speech_usage_recorded_once_when_close_all_is_retried(mock_tracker):
    bridge = _mk_bridge(mock_tracker, _named_tts("faketts"), input_mode="text")
    real_close = mock_tracker.close
    calls = []

    async def cancelling_close(**kw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("interrumpido tras el registro")
        return await real_close(**kw)

    mock_tracker.close = cancelling_close
    with pytest.raises(RuntimeError):
        await bridge.close_all()
    await bridge.close_all()
    assert len([e for e in mock_tracker.events if e.stage == "speech_usage"]) == 1
