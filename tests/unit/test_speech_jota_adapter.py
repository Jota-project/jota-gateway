"""Adaptador jota de los puertos de voz (spec 2026-10-20, fase 1)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import Response

from src.core.config import settings
from src.models.schemas import Client
from src.services.reconnection import ConnectionState
from src.services.speech.factory import build_stt_provider, build_tts_provider
from src.services.speech.jota import JotaSttProvider, JotaSttSession
from src.services.speech.ports import (
    SpeechToTextProvider,
    SttCallbacks,
    SttParams,
    SttSession,
    TextToSpeechProvider,
)
from src.services.transcriber_reconnecting import ReconnectingTranscriberClient
from src.services.tts_reconnecting import ReconnectingTTSClient

CLIENT = Client(id="c1", client_key="the-key", is_active=True)


def _callbacks() -> SttCallbacks:
    return SttCallbacks(AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock())


def test_factory_defaults_to_jota():
    assert build_stt_provider(settings).name == "jota"
    assert build_tts_provider(settings).name == "jota"




def test_jota_providers_satisfy_ports():
    stt = build_stt_provider(settings)
    assert isinstance(stt, SpeechToTextProvider)
    assert isinstance(build_tts_provider(settings), TextToSpeechProvider)
    cb = SttCallbacks(AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock())
    session = stt.open_session(
        client=Client(id="c1", client_key="k", is_active=True), params=SttParams("es", 0.0), callbacks=cb
    )
    assert isinstance(session, SttSession)


def _session(params=None, callbacks=None):
    cb = callbacks or _callbacks()
    provider = JotaSttProvider(settings)
    return provider.open_session(client=CLIENT, params=params or SttParams("en", 0.4), callbacks=cb), cb


@pytest.mark.asyncio
async def test_jota_session_start_calls_connect_with_params_and_token():
    session, _ = _session()
    session._inner.connect = AsyncMock()
    await session.start()
    session._inner.connect.assert_awaited_once_with(language="en", token="the-key", vad_thold=0.4)


@pytest.mark.asyncio
async def test_jota_session_run_passes_callbacks():
    session, cb = _session()
    session._inner.run = AsyncMock()
    await session.run()
    session._inner.run.assert_awaited_once_with(
        on_transcription_callback=cb.on_transcription,
        on_warning_callback=cb.on_warning,
        on_status_callback=cb.on_status,
        on_incomplete_callback=cb.on_incomplete,
    )


@pytest.mark.asyncio
async def test_jota_session_close_during_start_leaves_closed():
    """Foco 1: close() mientras start() aún conecta no deja un socket sin dueño."""
    session, _ = _session()
    inner = session._inner
    gate = asyncio.Event()
    opened = {"socket": False}

    async def slow_connect(**kw):
        await gate.wait()
        opened["socket"] = True

    async def close_client():
        opened["socket"] = False

    inner._client.connect = slow_connect
    inner._client.close = close_client

    start_task = asyncio.create_task(session.start())
    await asyncio.sleep(0)
    await session.close()
    gate.set()
    await start_task

    assert opened["socket"] is False
    assert inner._closed is True
    assert inner._run_task is None

    await session.close()  # idempotente


@pytest.mark.asyncio
async def test_jota_session_start_after_close_is_noop():
    session, _ = _session()
    session._inner.connect = AsyncMock()
    await session.close()
    await session.start()
    session._inner.connect.assert_not_awaited()


def test_jota_session_delegates_state_and_last_transcript():
    session, _ = _session()
    seen = []
    session.on_state_change = seen.append
    session._inner._set_state(ConnectionState.CONNECTED)
    assert session.state == ConnectionState.CONNECTED
    assert seen == [ConnectionState.CONNECTED]
    session._inner._client._last_transcription_at = 12.5
    assert session.last_transcript_at == 12.5
    assert isinstance(session, JotaSttSession)


@pytest.mark.asyncio
async def test_wrapper_last_error_has_no_exception_text():
    """Foco 4: last_error solo lleva el nombre del tipo, en los dos wrappers."""
    tw = ReconnectingTranscriberClient(url="x:1", client_id="c1")
    with patch.object(tw._client, "connect", new=AsyncMock(side_effect=OSError("secret-xyz"))):
        await tw.connect()
    assert tw.status().last_error == "OSError"
    assert "secret-xyz" not in tw.status().last_error

    tts = ReconnectingTTSClient(url="x:1", token="t")
    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        MockTTS.return_value.connect = AsyncMock(side_effect=OSError("secret-xyz"))
        assert await tts.connect(None, None, "c1") is None
    assert "secret-xyz" not in tts.status().last_error
    assert tts.status().last_error == "OSError"


@pytest.mark.asyncio
async def test_tts_probe_uses_ready_ping():
    tts = ReconnectingTTSClient(url="tts.test:8005", token="t")
    with respx.mock:
        respx.get("http://tts.test:8005/ready").mock(return_value=Response(200))
        assert await tts.probe() is True
    assert tts.name == "jota"
    assert tts.resolve_voice("af_heart") == "af_heart"
    await tts.start()
    await tts.aclose()


@pytest.mark.asyncio
async def test_stt_provider_probe_and_status():
    p = JotaSttProvider(settings)
    assert p.status().state == ConnectionState.CONNECTED  # nunca sondeado
    with patch(
        "src.services.speech.jota.TranscriberClient.ping", new=AsyncMock(return_value=False)
    ):
        assert await p.probe() is False
    s = p.status()
    assert (s.name, s.state, s.connected_at, s.reconnect_attempts, s.last_error) == (
        "transcriber",
        ConnectionState.DEGRADED,
        None,
        0,
        None,
    )
