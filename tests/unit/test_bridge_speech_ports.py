"""JotaBridge consume los puertos de voz (spec 2026-10-20, fase 1)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jota_gateway.models.schemas import ClientConfig
from jota_gateway.services.reconnection import ConnectionState
from jota_gateway.services.speech.ports import SttCapabilities
from tests.fakes.bridge import sent


class FakeSession:
    state = ConnectionState.CONNECTED
    on_state_change = None
    last_transcript_at = None

    def __init__(self, callbacks, emit_in_start=None):
        self.callbacks = callbacks
        self.emit_in_start = emit_in_start
        self.started = False

    async def start(self):
        self.started = True
        if self.emit_in_start:
            await self.callbacks.on_transcription(*self.emit_in_start)

    async def send_audio(self, frame):  # pragma: no cover
        pass

    async def send_end(self):  # pragma: no cover
        pass

    async def run(self):  # pragma: no cover
        pass

    async def close(self):  # pragma: no cover
        pass


class FakeStt:
    name = "fake"
    state = ConnectionState.CONNECTED
    on_state_change = None

    def __init__(self, partials=True, emit_in_start=None):
        self.capabilities = SttCapabilities(partials=partials, warmup_s=0.0)
        self.emit_in_start = emit_in_start
        self.opened = []
        self.session = None

    def open_session(self, *, client, params, callbacks):
        self.opened.append((client, params, callbacks))
        self.session = FakeSession(callbacks, self.emit_in_start)
        return self.session

    async def probe(self):  # pragma: no cover
        return True

    def status(self):  # pragma: no cover
        raise NotImplementedError

    async def start(self):  # pragma: no cover
        pass

    async def aclose(self):  # pragma: no cover
        pass


@pytest.fixture
def make_ports_bridge(make_bridge, mock_tracker):
    def _make(stt=None, **cfg):
        bridge, _ = make_bridge(
            client_id="c1",
            config=ClientConfig(stt_language="en", stt_vad_thold=0.3, **cfg),
            tracker=mock_tracker,
            input_mode="audio",
            output_mode=("audio", "text"),
            stt=stt,
        )
        return bridge

    return _make


async def test_bridge_uses_injected_stt_provider(make_ports_bridge):
    stt = FakeStt()
    bridge = make_ports_bridge(stt)
    await bridge.connect_internal_services()
    assert len(stt.opened) == 1
    client, params, _ = stt.opened[0]
    assert client is bridge.client
    assert params.language == bridge.config.stt_language == "en"
    assert params.vad_threshold == 0.3
    assert stt.session.started is True
    assert bridge.transcriber is stt.session


async def test_stt_callbacks_available_before_run(make_ports_bridge):
    """Foco 5: un evento emitido dentro de start() llega al bridge, sin run()."""
    stt = FakeStt(emit_in_start=("hola", False))
    bridge = make_ports_bridge(stt, barge_in_enabled=False)
    await bridge.connect_internal_services()
    assert {"type": "transcription_partial", "text": "hola"} in await sent(bridge)


async def test_barge_in_capability_false_without_partials(make_ports_bridge):
    bridge = make_ports_bridge(FakeStt(partials=False))
    bridge.transcriber = MagicMock(state=ConnectionState.CONNECTED)
    bridge.tts.probe = AsyncMock(return_value=True)
    bridge.orchestrator.ping = AsyncMock(return_value=True)
    result = await bridge.health_check()
    assert result == {"barge_in": False, "tts": True, "transcriber": True}
