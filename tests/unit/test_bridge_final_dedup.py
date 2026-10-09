"""Final-transcription dedup is time-windowed (issue #128).

The transcriber used to emit the same `is_final` twice within milliseconds
(jota-transcriber#27, fixed upstream in #28); the gateway kept a defensive dedup.
It must only swallow such near-simultaneous repeats — a user legitimately saying
the same thing again ("sí… sí") must still reach the client.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import src.services.bridge as bridge_module
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry

_CLIENT = Client(id="c1", client_key="k", is_active=True)


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(bridge_module.time, "monotonic", lambda: now[0])
    return now


@pytest.fixture
def bridge(mock_tracker):
    ws = AsyncMock()
    b = JotaBridge(
        client=_CLIENT,
        config=ClientConfig(),
        client_ws=ws,
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=mock_tracker,
        handshake=Handshake(client_key="k", input_mode="audio", output_mode=["text"]),
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    b.transcriber = MagicMock()
    return b


def _finals_sent(bridge):
    return [
        c.args[0]
        for c in bridge.client_ws.send_json.await_args_list
        if c.args[0].get("type") == "transcription"
    ]


async def test_identical_final_within_window_is_dropped(bridge, clock):
    await bridge._on_transcription("hola", True)
    clock[0] += 0.2
    await bridge._on_transcription("hola", True)

    assert len(_finals_sent(bridge)) == 1


async def test_identical_final_after_window_is_delivered(bridge, clock):
    await bridge._on_transcription("sí", True)
    clock[0] += 3.0
    await bridge._on_transcription("sí", True)

    assert len(_finals_sent(bridge)) == 2


async def test_different_final_within_window_is_delivered(bridge, clock):
    await bridge._on_transcription("hola", True)
    clock[0] += 0.1
    await bridge._on_transcription("adiós", True)

    assert [m["text"] for m in _finals_sent(bridge)] == ["hola", "adiós"]


async def test_dropped_duplicate_does_not_extend_the_window(bridge, clock):
    """Window is measured from the last *delivered* final, so a stream of
    repeats can't keep suppressing a legitimate one indefinitely."""
    await bridge._on_transcription("sí", True)
    clock[0] += 0.6
    await bridge._on_transcription("sí", True)  # dropped (0.6s after delivered)
    clock[0] += 0.6
    await bridge._on_transcription("sí", True)  # 1.2s after delivered -> delivered

    assert len(_finals_sent(bridge)) == 2
