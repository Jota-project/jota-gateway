"""Contrato de todo SpeechToTextProvider (se ejecuta por adaptador)."""

import asyncio
import logging
import struct
import time

from src.services.speech.ports import SttCallbacks, SttParams

FRAME = struct.pack("<160f", *([0.1] * 160))


def _callbacks(events: list):
    async def on_tr(text, is_final):
        events.append((text, is_final))

    async def noop(*_a):
        return None

    return SttCallbacks(on_transcription=on_tr, on_warning=noop, on_status=noop, on_incomplete=noop)


async def _open(h, events):
    session = h.provider.open_session(
        client=h.client,
        params=SttParams(language="es", vad_threshold=0.0),
        callbacks=_callbacks(events),
    )
    await session.start()
    return session


async def _wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return pred()


async def _finish(session, runner):
    """close() puede cancelar run(): ambas salidas (retorno o cancelación) son válidas."""
    await session.close()
    await asyncio.wait_for(asyncio.gather(runner, return_exceptions=True), 2)


async def test_partial_then_final_delivered_in_order(stt_harness):
    stt_harness.upstream.on_audio = [("hola", False), ("hola mundo", True)]
    events: list = []
    session = await _open(stt_harness, events)
    runner = asyncio.create_task(session.run())
    try:
        await session.send_audio(FRAME)
        assert await _wait_for(lambda: len(events) >= 2)
        assert events[:2] == [("hola", False), ("hola mundo", True)]
    finally:
        await _finish(session, runner)


async def test_send_end_produces_final(stt_harness):
    stt_harness.upstream.on_end = [("fin de frase", True)]
    events: list = []
    session = await _open(stt_harness, events)
    runner = asyncio.create_task(session.run())
    try:
        await session.send_audio(FRAME)
        await session.send_end()
        assert await _wait_for(lambda: ("fin de frase", True) in events)
    finally:
        await _finish(session, runner)


async def test_close_is_idempotent(stt_harness):
    session = await _open(stt_harness, [])
    await session.close()
    await session.close()


async def test_callbacks_receive_events_before_run_is_awaited_long(stt_harness):
    """Los callbacks se dan en open_session: basta con lanzar run(), sin esperarlo."""
    stt_harness.upstream.on_audio = [("pronto", False)]
    events: list = []
    session = await _open(stt_harness, events)
    runner = asyncio.create_task(session.run())
    try:
        await session.send_audio(FRAME)
        assert await _wait_for(lambda: events == [("pronto", False)], timeout=1.0)
        assert not runner.done()
    finally:
        await _finish(session, runner)


async def test_no_secret_or_text_in_logs(stt_harness, caplog):
    from tests.contract.conftest import SECRET_KEY, USER_TEXT

    stt_harness.upstream.on_audio = [(USER_TEXT, True)]
    events: list = []
    with caplog.at_level(logging.DEBUG):
        session = await _open(stt_harness, events)
        runner = asyncio.create_task(session.run())
        try:
            await session.send_audio(FRAME)
            assert await _wait_for(lambda: events)
        finally:
            await _finish(session, runner)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert SECRET_KEY not in logged
    assert USER_TEXT not in logged
