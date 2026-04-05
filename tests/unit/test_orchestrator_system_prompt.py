"""Tests for OrchestratorClient system_prompt_extra in payload."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.orchestrator_client import OrchestratorClient


def _make_client() -> OrchestratorClient:
    return OrchestratorClient(
        base_url="localhost:8000",
        api_key="key",
        client_id="cid",
    )


def _mock_http(lines: list[str]):
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()

    async def aiter_lines():
        for line in lines:
            yield line

    mock_response.aiter_lines = aiter_lines
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_http = AsyncMock()
    mock_http.stream = MagicMock(return_value=mock_response)
    return mock_http


async def test_payload_has_no_system_prompt_extra_by_default():
    """system_prompt_extra absent from payload when not provided."""
    c = _make_client()
    c._http = _mock_http(['{"type":"token","content":"hi"}'])

    _ = [e async for e in c.stream_response("hello")]

    payload = c._http.stream.call_args.kwargs["json"]
    assert "system_prompt_extra" not in payload


async def test_payload_includes_system_prompt_extra_when_set():
    """system_prompt_extra in payload when provided."""
    c = _make_client()
    c._http = _mock_http(['{"type":"token","content":"hi"}'])

    _ = [e async for e in c.stream_response("hello", system_prompt_extra="be brief")]

    payload = c._http.stream.call_args.kwargs["json"]
    assert payload["system_prompt_extra"] == "be brief"


async def test_payload_omits_system_prompt_extra_when_none():
    """None explicitly passed → field not included."""
    c = _make_client()
    c._http = _mock_http(['{"type":"token","content":"hi"}'])

    _ = [e async for e in c.stream_response("hello", system_prompt_extra=None)]

    payload = c._http.stream.call_args.kwargs["json"]
    assert "system_prompt_extra" not in payload


async def test_listen_loop_forwards_system_prompt_extra():
    """listen_loop passes system_prompt_extra down to stream_response."""
    c = _make_client()
    c._http = _mock_http(['{"type":"token","content":"hi"}'])

    await c.listen_loop(
        "hello",
        on_token=AsyncMock(),
        on_event=AsyncMock(),
        system_prompt_extra="be brief",
    )

    payload = c._http.stream.call_args.kwargs["json"]
    assert payload["system_prompt_extra"] == "be brief"
