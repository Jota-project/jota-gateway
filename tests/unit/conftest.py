import logging

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.pipeline_tracker import PipelineTracker


@pytest.fixture(autouse=True)
def _reenable_app_loggers():
    """Keep `src.*` loggers enabled so caplog-based assertions are hermetic.

    Integration tests run `run_migrations()`, whose Alembic `env.py` calls
    `logging.config.fileConfig()` with the default `disable_existing_loggers=True`.
    That sets `disabled=True` on every already-imported `src.*` logger, and the
    flag leaks across tests because logging config is process-global — silently
    suppressing later unit tests that assert on captured DEBUG/INFO output.
    Re-enable them before each unit test. (Root cause: migrations/env.py — a
    candidate standalone fix would pass `disable_existing_loggers=False` there.)
    """
    for name, obj in logging.root.manager.loggerDict.items():
        if name.startswith("src.") and isinstance(obj, logging.Logger):
            obj.disabled = False
    yield


@pytest.fixture
def mock_tracker():
    ws = AsyncMock()
    registry = MagicMock()
    return PipelineTracker(
        session_id="test:unit",
        client_id="test",
        input_mode="text",
        output_mode=["text"],
        client_ws=ws,
        registry=registry,
    )
