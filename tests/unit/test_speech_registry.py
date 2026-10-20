"""Cambio de proveedor de voz en caliente (SpeechProviders, fase 2)."""

from unittest.mock import AsyncMock

import pytest

from src.core.config import Settings
from src.core.startup_checks import SpeechConfigError
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.reconnection import ConnectionState
from src.services.speech import factory
from src.services.speech.jota import JotaSttProvider
from src.services.speech.registry import SpeechProviders, SpeechSwapError
from src.services.speech.sessions import SessionRefCount
from tests.unit.test_bridge_speech_ports import FakeSession, FakeStt


class NamedStt(FakeStt):
    def __init__(self, name):
        super().__init__()
        self.name = name
        self.started = 0
        self.closed = 0

    async def start(self):
        self.started += 1

    async def retire(self):
        await self.aclose()

    async def aclose(self):
        self.closed += 1


class NamedTts:
    state = ConnectionState.CONNECTED
    on_state_change = None

    def __init__(self, name):
        self.name = name
        self.started = 0
        self.closed = 0
        self.connects = 0

    async def connect(self, voice, speed, client_id):
        self.connects += 1
        return None

    async def probe(self):  # pragma: no cover
        return True

    def status(self):  # pragma: no cover
        raise NotImplementedError

    def resolve_voice(self, configured):  # pragma: no cover
        return configured

    async def start(self):
        self.started += 1

    async def aclose(self):
        self.closed += 1


@pytest.fixture
def s():
    return Settings(_env_file=None, ELEVENLABS_API_KEY=None)


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setitem(factory.PROVIDER_BUILDERS["stt"], "fake", lambda settings: NamedStt("fake"))
    monkeypatch.setitem(factory.PROVIDER_BUILDERS["tts"], "fake", lambda settings: NamedTts("fake"))


def _providers(s):
    return SpeechProviders(s, NamedStt("jota"), NamedTts("jota"))


def _bridge(providers, mock_tracker):
    return JotaBridge(
        client=Client(id="c1", client_key="k", is_active=True),
        config=ClientConfig(),
        client_ws=AsyncMock(),
        orchestrator=AsyncMock(),
        tts=providers.tts,
        stt=providers.stt,
        speech=providers,
        tracker=mock_tracker,
        handshake=Handshake(
            client_key="k", input_mode="audio", output_mode=["audio", "text"], agent="main"
        ),
        client_registry=ClientRegistry(),
        default_agent="main",
    )


async def test_swap_tts_new_turns_use_new_provider(s, fakes, mock_tracker):
    p = _providers(s)
    old_tts = p.tts
    b = _bridge(p, mock_tracker)
    assert b.tts is old_tts
    result = await p.swap(tts_name="fake")
    assert result == {"stt": "jota", "tts": "fake"}
    assert b.tts is p.tts and b.tts.name == "fake"
    assert b.tts.started == 1 and old_tts.closed == 1


async def test_swap_stt_existing_session_keeps_old_provider_new_session_uses_new(
    s, fakes, mock_tracker
):
    p = _providers(s)
    old = p.stt
    b1 = _bridge(p, mock_tracker)
    await b1.connect_internal_services()
    await p.swap(stt_name="fake")
    b2 = _bridge(p, mock_tracker)
    await b2.connect_internal_services()
    assert len(old.opened) == 1 and b1.transcriber is old.session
    assert p.stt.name == "fake" and len(p.stt.opened) == 1
    assert isinstance(b2.transcriber, FakeSession) and b2.transcriber is p.stt.session


async def test_old_stt_provider_closed_when_last_session_closes(s):
    class Counting(JotaSttProvider):
        closed = 0

        async def aclose(self):
            Counting.closed += 1

    prov = Counting(s)
    client = Client(id="c1", client_key="k", is_active=True)
    from src.services.speech.ports import SttCallbacks, SttParams

    async def noop(*_a):
        return None

    cb = SttCallbacks(noop, noop, noop, noop)
    sess1 = prov.open_session(client=client, params=SttParams("es", 0.0), callbacks=cb)
    sess2 = prov.open_session(client=client, params=SttParams("es", 0.0), callbacks=cb)
    await prov.retire()
    assert Counting.closed == 0
    await sess1.close()
    await sess1.close()  # idempotente: no decrementa dos veces
    assert Counting.closed == 0
    await sess2.close()
    assert Counting.closed == 1

    idle = Counting(s)
    await idle.retire()
    assert Counting.closed == 2


async def test_swap_rejected_without_key_keeps_current(s, fakes):
    p = _providers(s)
    stt, tts = p.stt, p.tts
    with pytest.raises(SpeechConfigError) as e:
        await p.swap(stt_name="elevenlabs", tts_name="fake")
    assert "ELEVENLABS_API_KEY" in str(e.value)
    assert p.stt is stt and p.tts is tts and tts.closed == 0


async def test_swap_same_name_is_noop(s, fakes):
    p = _providers(s)
    stt, tts = p.stt, p.tts
    assert await p.swap(stt_name="jota", tts_name="jota") == {"stt": "jota", "tts": "jota"}
    assert p.stt is stt and p.tts is tts
    assert stt.closed == 0 and tts.closed == 0 and stt.started == 0


async def test_swap_rewires_state_listeners(s, fakes):
    p = _providers(s)
    on_stt, on_tts = object(), object()
    p.set_state_listeners(on_stt, on_tts)
    await p.swap(stt_name="fake", tts_name="fake")
    assert p.stt.on_state_change is on_stt and p.tts.on_state_change is on_tts


async def test_swap_unknown_provider_raises_value_error_and_keeps_current(s, fakes):
    p = _providers(s)
    tts = p.tts
    with pytest.raises(ValueError):
        await p.swap(stt_name="nope")
    assert p.tts is tts


class RefCountedStt(FakeStt):
    """Proveedor no-jota cuyo aclose() rompe sus sesiones abiertas."""

    name = "refcounted"

    def __init__(self):
        super().__init__()
        self.refs = SessionRefCount()
        self.closed = 0
        self.broken = False

    def open_session(self, *, client, params, callbacks):
        s = super().open_session(client=client, params=params, callbacks=callbacks)
        self.refs.opened()

        async def close():
            await self.refs.closed()

        s.close = close
        return s

    async def retire(self):
        await self.refs.retire(self.aclose)

    async def aclose(self):
        self.closed += 1
        self.broken = True


async def test_swap_stt_open_session_survives_until_closed_and_aclose_once(s, fakes):
    old = RefCountedStt()
    p = SpeechProviders(s, old, NamedTts("jota"))
    sess = old.open_session(client=None, params=None, callbacks=None)
    await p.swap(stt_name="fake")
    assert old.broken is False and old.closed == 0
    await sess.close()
    await sess.close()
    assert old.closed == 1 and old.broken is True


async def test_session_refcount_fires_once_and_immediately_when_idle():
    calls = []

    async def cb():
        calls.append(1)

    idle = SessionRefCount()
    await idle.retire(cb)
    await idle.retire(cb)
    assert calls == [1]

    busy = SessionRefCount()
    busy.opened()
    await busy.retire(cb)
    assert calls == [1]
    await busy.closed()
    await busy.closed()
    assert calls == [1, 1]


async def test_swap_detaches_listeners_from_replaced_providers(s, fakes):
    p = _providers(s)
    old_tts, old_stt = p.tts, p.stt
    heard = []
    p.set_state_listeners(heard.append, heard.append)
    await p.swap(stt_name="fake", tts_name="fake")
    assert old_tts.on_state_change is None and old_stt.on_state_change is None


class FailingStartTts(NamedTts):
    async def start(self):
        raise RuntimeError("secret-detail")


async def test_swap_failed_start_keeps_current_and_closes_new(s, monkeypatch):
    made = []

    def build(settings):
        t = FailingStartTts("bad")
        made.append(t)
        return t

    monkeypatch.setitem(factory.PROVIDER_BUILDERS["tts"], "bad", build)
    p = _providers(s)
    stt, tts = p.stt, p.tts
    with pytest.raises(SpeechSwapError) as e:
        await p.swap(tts_name="bad")
    assert "secret-detail" not in str(e.value)
    assert e.value.kind == "tts" and e.value.provider == "bad"
    assert p.stt is stt and p.tts is tts and tts.closed == 0
    assert made[0].closed == 1


async def test_swap_failed_stt_start_closes_both_new_providers(s, monkeypatch):
    class BadStt(NamedStt):
        async def start(self):
            raise RuntimeError("x")

    new_tts = NamedTts("fake")
    stt_made = BadStt("bad")
    monkeypatch.setitem(factory.PROVIDER_BUILDERS["stt"], "bad", lambda settings: stt_made)
    monkeypatch.setitem(factory.PROVIDER_BUILDERS["tts"], "fake", lambda settings: new_tts)
    p = _providers(s)
    tts = p.tts
    with pytest.raises(SpeechSwapError):
        await p.swap(stt_name="bad", tts_name="fake")
    assert p.tts is tts and stt_made.closed == 1 and new_tts.closed == 1
