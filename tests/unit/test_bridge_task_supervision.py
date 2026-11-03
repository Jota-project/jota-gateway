"""Session tasks are supervised: a crash is logged, not silent (issue #131)."""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from jota_gateway.models.schemas import ClientConfig, Handshake
from jota_gateway.services.reconnection import ConnectionState


@pytest.fixture
async def bridge(make_bridge):
    b, _ = make_bridge(client_id="c1")
    return b


async def test_crashed_session_task_is_logged_with_its_name(bridge, caplog):
    async def boom():
        raise RuntimeError("kaboom")

    task = asyncio.create_task(boom(), name="boom-task")
    bridge._supervise(task)

    with caplog.at_level(logging.ERROR, logger="jota_gateway.services.bridge"):
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    # #238: nombre de la tarea y tipo de la excepción, nunca su mensaje.
    assert any("boom-task" in m and "RuntimeError" in m for m in msgs)
    assert not any("kaboom" in m for m in msgs)


async def test_cancelled_session_task_is_not_logged_as_error(bridge, caplog):
    task = asyncio.create_task(asyncio.sleep(60), name="sleeper")
    bridge._supervise(task)

    with caplog.at_level(logging.ERROR, logger="jota_gateway.services.bridge"):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_transcriber_state_notice_failure_is_logged(bridge, caplog):
    # #221: the fire-and-forget status task is supervised (type only).
    bridge.notify_service_status = AsyncMock(side_effect=RuntimeError("kaboom"))
    with caplog.at_level(logging.ERROR, logger="jota_gateway.services.bridge"):
        bridge._on_transcriber_state_change(ConnectionState.CONNECTED)
        await _settle()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("RuntimeError" in m for m in msgs)
    assert not any("kaboom" in m for m in msgs)
    assert not bridge._background_tasks


async def test_idle_watchdog_closer_failure_is_logged(bridge, caplog):
    bridge.close_all = AsyncMock(side_effect=RuntimeError("kaboom"))

    async def boom():
        raise RuntimeError("watchdog")

    with caplog.at_level(logging.ERROR, logger="jota_gateway.services.bridge"):
        task = asyncio.create_task(boom(), name="idle_watchdog")
        bridge._supervise(task)
        await asyncio.gather(task, return_exceptions=True)
        await _settle()
    msgs = [r.getMessage() for r in caplog.records if r.name == "jota_gateway.services.bridge"]
    assert any("idle_watchdog_close" in m and "RuntimeError" in m for m in msgs)
    assert not any("kaboom" in m for m in msgs)


async def test_push_audio_failure_is_logged(bridge, caplog):
    # #221: the push audio task can be dropped (_drop_push_tts) without anyone
    # retrieving its outcome; its failure must still be logged.
    bridge.config = ClientConfig(push_enabled=True)
    bridge.handshake = Handshake(client_key="k", input_mode="text", output_mode=["audio"])
    tts = AsyncMock()

    async def broken_stream():
        raise RuntimeError("kaboom")
        yield b""

    tts.get_audio_stream = broken_stream
    bridge.tts.connect = AsyncMock(return_value=tts)
    with caplog.at_level(logging.WARNING, logger="jota_gateway.services.bridge"):
        await bridge.on_push_turn_start("sk")
        await _settle()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("RuntimeError" in m for m in msgs)
    assert not any("kaboom" in m for m in msgs)
