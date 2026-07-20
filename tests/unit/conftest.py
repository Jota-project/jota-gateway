import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.pipeline_tracker import PipelineTracker


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
