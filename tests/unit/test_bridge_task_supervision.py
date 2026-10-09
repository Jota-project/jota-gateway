"""Session tasks are supervised: a crash is logged, not silent (issue #131)."""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry


@pytest.fixture
def bridge(mock_tracker):
    return JotaBridge(
        client=Client(id="c1", client_key="k", is_active=True),
        config=ClientConfig(),
        client_ws=AsyncMock(),
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=mock_tracker,
        handshake=Handshake(client_key="k", input_mode="text", output_mode=["text"]),
        client_registry=ClientRegistry(),
        default_agent="main",
    )


async def test_crashed_session_task_is_logged_with_its_name(bridge, caplog):
    async def boom():
        raise RuntimeError("kaboom")

    task = asyncio.create_task(boom(), name="boom-task")
    bridge._supervise(task)

    with caplog.at_level(logging.ERROR, logger="src.services.bridge"):
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("boom-task" in m and "kaboom" in m for m in msgs)


async def test_cancelled_session_task_is_not_logged_as_error(bridge, caplog):
    task = asyncio.create_task(asyncio.sleep(60), name="sleeper")
    bridge._supervise(task)

    with caplog.at_level(logging.ERROR, logger="src.services.bridge"):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
