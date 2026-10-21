"""JotaBridge consume los puertos de voz (spec 2026-10-20, fase 1)."""

from unittest.mock import AsyncMock, MagicMock

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.reconnection import ConnectionState
from src.services.speech.jota import JotaSttProvider, JotaSttSession
from src.services.speech.ports import SttCapabilities


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

    async def retire(self):  # pragma: no cover
        pass

    async def aclose(self):  # pragma: no cover
        pass


def _bridge(mock_tracker, stt=None, **cfg):
    kwargs = {} if stt is None else {"stt": stt}
    return JotaBridge(
        client=Client(id="c1", client_key="k", is_active=True),
        config=ClientConfig(stt_language="en", stt_vad_thold=0.3, **cfg),
        client_ws=AsyncMock(),
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=mock_tracker,
        handshake=Handshake(client_key="k", input_mode="audio", output_mode=["audio", "text"]),
        client_registry=ClientRegistry(),
        default_agent="main",
        **kwargs,
    )


async def test_bridge_uses_injected_stt_provider(mock_tracker):
    stt = FakeStt()
    bridge = _bridge(mock_tracker, stt)
    await bridge.connect_internal_services()
    assert len(stt.opened) == 1
    client, params, _ = stt.opened[0]
    assert client is bridge.client
    assert params.language == bridge.config.stt_language == "en"
    assert params.vad_threshold == 0.3
    assert stt.session.started is True
    assert bridge.transcriber is stt.session


async def test_bridge_defaults_stt_provider_when_not_injected(mock_tracker):
    """Foco 2: sin `stt=` el bridge sigue funcionando con el proveedor por defecto."""
    bridge = _bridge(mock_tracker)
    assert isinstance(bridge._stt, JotaSttProvider)
    await bridge.connect_internal_services()
    try:
        assert isinstance(bridge.transcriber, JotaSttSession)
    finally:
        await bridge.transcriber.close()


async def test_stt_callbacks_available_before_run(mock_tracker):
    """Foco 5: un evento emitido dentro de start() llega al bridge, sin run()."""
    stt = FakeStt(emit_in_start=("hola", False))
    bridge = _bridge(mock_tracker, stt, barge_in_enabled=False)
    await bridge.connect_internal_services()
    sent = [c.args[0] for c in bridge.client_ws.send_json.await_args_list]
    assert {"type": "transcription_partial", "text": "hola"} in sent


async def test_barge_in_capability_false_without_partials(mock_tracker):
    bridge = _bridge(mock_tracker, FakeStt(partials=False))
    bridge.transcriber = MagicMock(state=ConnectionState.CONNECTED)
    bridge.tts.probe = AsyncMock(return_value=True)
    bridge.orchestrator.ping = AsyncMock(return_value=True)
    result = await bridge.health_check()
    assert result == {"barge_in": False, "tts": True, "transcriber": True}
