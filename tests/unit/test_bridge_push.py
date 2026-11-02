import asyncio
import functools
import logging
from unittest.mock import AsyncMock

import pytest

from jota_gateway.models.schemas import ClientConfig
from jota_gateway.services.openclaw.registry import ClientRegistry
from tests.fakes.bridge import sent, wait_until


@pytest.fixture
def make_push_bridge(make_bridge):
    return functools.partial(make_bridge, client_id="hab_sito")


async def test_connect_registers_in_client_registry(make_push_bridge):
    registry = ClientRegistry()
    bridge, _ = make_push_bridge(client_registry=registry)
    await bridge.connect_internal_services()
    assert registry.get("hab_sito") is bridge


async def test_close_unregisters_from_client_registry(make_push_bridge):
    registry = ClientRegistry()
    bridge, _ = make_push_bridge(client_registry=registry)
    await bridge.connect_internal_services()
    await bridge.close_all()
    assert registry.get("hab_sito") is None


async def test_deliver_push_text_sends_push_message(make_push_bridge):
    bridge, _ = make_push_bridge(output_mode=("text",))
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": "Buenos días!"}
    await bridge.deliver_push(payload)
    assert await sent(bridge) == [
        {
            "type": "token",
            "turn_id": "t-1",
            "text": "Buenos días!",
        }
    ]


async def test_deliver_push_empty_delta_ignored(make_push_bridge):
    bridge, _ = make_push_bridge(output_mode=("text",))
    bridge._push_turn_open = True
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": ""}
    await bridge.deliver_push(payload)
    assert await sent(bridge) == []


async def test_on_push_turn_start_no_audio_does_nothing(make_push_bridge):
    bridge, _ = make_push_bridge(output_mode=("text",))
    await bridge.on_push_turn_start("agent:main:hab_sito")
    assert bridge._push_tts is None


async def test_on_push_turn_end_closes_push_tts(make_push_bridge):
    bridge, _ = make_push_bridge(output_mode=("audio", "text"))
    mock_tts = AsyncMock()
    bridge._push_tts = mock_tts
    bridge._push_audio_task = None  # no audio task in this scenario
    bridge._push_turn_open = True  # turn_end only closes TTS if a turn is open (issue #84)
    await bridge.on_push_turn_end("agent:main:hab_sito")
    mock_tts.end.assert_awaited_once()
    mock_tts.close.assert_awaited_once()
    assert bridge._push_tts is None


async def aiter(items):
    for item in items:
        yield item


async def test_on_push_turn_start_audio_creates_tts_and_pipe(make_push_bridge):
    mock_tts_client = AsyncMock()
    mock_tts_client.get_audio_stream = lambda: aiter([])
    tts_wrapper = AsyncMock()
    tts_wrapper.connect = AsyncMock(return_value=mock_tts_client)

    bridge, _ = make_push_bridge(output_mode=("audio", "text"), tts=tts_wrapper)
    await bridge.on_push_turn_start("agent:main:hab_sito")

    tts_wrapper.connect.assert_awaited_once()
    assert bridge._push_tts is mock_tts_client
    assert bridge._push_audio_task is not None


async def test_push_audio_pipe_forwards_chunks_with_header(make_push_bridge):
    """Audio chunks from TTS include [0xA1][turn_seq uint16 BE] header."""
    chunks = [b"\x00\x01", b"\x02\x03"]
    mock_tts_client = AsyncMock()
    mock_tts_client.get_audio_stream = lambda: aiter(chunks)
    tts_wrapper = AsyncMock()
    tts_wrapper.connect = AsyncMock(return_value=mock_tts_client)

    bridge, ws = make_push_bridge(output_mode=("audio", "text"), tts=tts_wrapper)
    await bridge.on_push_turn_start("agent:main:hab_sito")
    await wait_until(lambda: sum(isinstance(m, bytes) for m in ws.sent) == 2)
    audio = [m for m in ws.sent if isinstance(m, bytes)]
    expected_header = bytes([0xA1]) + (1).to_bytes(2, "big")
    assert audio == [expected_header + b"\x00\x01", expected_header + b"\x02\x03"]


async def test_on_push_turn_start_sends_turn_start_message(make_push_bridge):
    """on_push_turn_start sends turn_start JSON message to client."""
    bridge, _ = make_push_bridge(output_mode=("text",))
    await bridge.on_push_turn_start("agent:main:hab_sito")
    assert await sent(bridge) == [
        {
            "type": "turn_start",
            "turn_id": "t-1",
            "turn_seq": 1,
        }
    ]


async def test_on_push_turn_end_sends_turn_end_message(make_push_bridge):
    """on_push_turn_end sends turn_end JSON message to client."""
    bridge, _ = make_push_bridge(output_mode=("text",))
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = (
        True  # a turn_end is only valid if a turn_start came before (issue #84)
    )
    await bridge.on_push_turn_end("agent:main:hab_sito")
    assert await sent(bridge) == [
        {
            "type": "turn_end",
            "turn_id": "t-1",
        }
    ]


async def test_deliver_push_with_tts_sends_to_tts(make_push_bridge):
    bridge, _ = make_push_bridge(output_mode=("audio", "text"))
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True
    mock_tts = AsyncMock()
    bridge._push_tts = mock_tts
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": "Hola!"}
    await bridge.deliver_push(payload)
    mock_tts.send_text_chunk.assert_awaited_once_with("Hola!")


async def test_on_push_turn_end_no_tts_is_noop(make_push_bridge):
    bridge, _ = make_push_bridge()
    assert bridge._push_tts is None
    await bridge.on_push_turn_end("agent:main:hab_sito")  # must not raise
    assert bridge._push_tts is None


async def test_push_disabled_ignores_push_turn_start(make_push_bridge):
    """push_enabled=False hace que on_push_turn_start no haga nada."""
    bridge, _ = make_push_bridge(client_id="ha", config=ClientConfig(push_enabled=False))
    await bridge.on_push_turn_start("agent:main:ha")
    assert await sent(bridge) == []
    assert bridge._push_turn_id is None


async def test_deliver_push_tool_call_forwarded_when_enabled(make_push_bridge):
    bridge, _ = make_push_bridge(config=ClientConfig(tool_calls_enabled=True))
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True
    data = {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {"command": "ls"}}
    await bridge.deliver_push_tool_call(data)
    assert await sent(bridge) == [
        {
            "type": "tool_call",
            "turn_id": "t-1",
            "phase": "start",
            "name": "exec",
            "tool_call_id": "call-1",
            "args": {"command": "ls"},
            "result": None,
            "is_error": None,
        }
    ]


async def test_deliver_push_tool_call_not_forwarded_when_disabled(make_push_bridge):
    bridge, _ = make_push_bridge(config=ClientConfig(tool_calls_enabled=False))
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True
    data = {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {}}
    await bridge.deliver_push_tool_call(data)
    assert await sent(bridge) == []


async def test_deliver_push_tool_call_malformed_payload_ignored(make_push_bridge):
    bridge, _ = make_push_bridge(config=ClientConfig(tool_calls_enabled=True))
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True
    await bridge.deliver_push_tool_call({"phase": "update"})  # must not raise
    assert await sent(bridge) == []


async def test_multiple_push_starts_and_ends_collapse_to_single_pair(make_push_bridge):
    """Reproduces issue #84 verbatim from the jota-voice client log:

    ```
    turn_start t-1 (seq=1)
    turn_start t-2 (seq=2)
    turn_start t-3 (seq=3)
    turn_start t-4 (seq=4)
    turn_end   t-4 (×3)
    ```

    OpenClaw emits N agent starts back-to-back without intermediate agent
    ends (each step of the agent's reasoning opens its own agent start
    immediately, while the previous one is still mid-flight), then a
    cluster of agent ends arrives at the end. Without the collapse fix the
    bridge increments _turn_seq on every start and emits one turn_start
    per start, and on every agent end emits a turn_end with whatever
    _push_turn_id was last set (which by then is t-4) — producing the
    exact pattern the client reported.

    With the fix: only the first start opens the logical turn, the rest
    are dropped, and only the first end emits turn_end. End events that
    arrive after the turn is already closed are also dropped.
    """
    bridge, ws = make_push_bridge(output_mode=("text",))

    # The exact sequence from the issue log: 4 starts in a row, then 3 ends.
    await bridge.on_push_turn_start("agent:main:hab_sito")
    await bridge.on_push_turn_start("agent:main:hab_sito")
    await bridge.on_push_turn_start("agent:main:hab_sito")
    await bridge.on_push_turn_start("agent:main:hab_sito")
    await bridge.on_push_turn_end("agent:main:hab_sito")
    await bridge.on_push_turn_end("agent:main:hab_sito")
    await bridge.on_push_turn_end("agent:main:hab_sito")

    await sent(bridge)
    starts = ws.of("turn_start")
    ends = ws.of("turn_end")

    assert len(starts) == 1, f"expected 1 turn_start, got {len(starts)}: {starts}"
    assert len(ends) == 1, f"expected 1 turn_end, got {len(ends)}: {ends}"
    assert starts[0]["turn_id"] == ends[0]["turn_id"] == "t-1", (
        f"all frames must share turn_id t-1, got: {ws.sent}"
    )


async def test_first_push_start_assigns_turn_seq_subsequent_does_not(make_push_bridge):
    """Issue #84 — only the FIRST agent start opens a logical turn and
    consumes a turn_seq. Subsequent agent starts (intermediate tool steps)
    must not increment _turn_seq further, so the client sees one stable
    turn_id for the whole multi-step reply.
    """
    bridge, ws = make_push_bridge(output_mode=("text",))
    assert bridge._turn_seq == 0

    await bridge.on_push_turn_start("agent:main:hab_sito")
    assert bridge._turn_seq == 1
    assert bridge._push_turn_id == "t-1"

    await bridge.on_push_turn_start("agent:main:hab_sito")
    await bridge.on_push_turn_start("agent:main:hab_sito")

    # Wire-level check: after 3 starts, the client must see exactly one turn_start.
    # Strengthens the test — without the fix, three start frames would be sent.
    await sent(bridge)
    starts = ws.of("turn_start")
    assert len(starts) == 1, f"expected 1 turn_start after 3 starts, got {len(starts)}: {starts}"

    assert bridge._turn_seq == 1, (
        f"only the first agent start should increment _turn_seq; "
        f"after 3 starts it is {bridge._turn_seq}"
    )
    assert bridge._push_turn_id == "t-1"


async def test_on_push_turn_end_with_no_open_turn_does_not_emit(make_push_bridge):
    """Issue #84 — an agent end arriving without a matching agent start
    (orphan, or after close_all reset) must not emit a turn_end frame to
    the client. Guards the early-return branch added to on_push_turn_end.
    """
    bridge, _ = make_push_bridge(output_mode=("text",))
    assert bridge._push_turn_open is False
    bridge._push_turn_id = "t-1"  # simulate stale state from before reset

    await bridge.on_push_turn_end("agent:main:hab_sito")

    assert await sent(bridge) == [], "on_push_turn_end with no open turn must not emit any frame"
    # Flag must stay False (we never set it True).
    assert bridge._push_turn_open is False


_TOKEN = {"sessionKey": "agent:main:hab_sito", "deltaText": "Buenos días!"}
_TOOL = {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {"command": "ls"}}


@pytest.mark.parametrize(
    ("deliver", "payload", "push_enabled", "stale_open"),
    [
        # #109: push_enabled=False blocks it even with a stale/inconsistent open turn
        pytest.param("deliver_push", _TOKEN, False, True, id="token-push-disabled"),
        # #109 core bug: no turn_start was ever sent, so a frame would leak outside
        # any turn_start/turn_end envelope with a stale/None turn_id
        pytest.param("deliver_push", _TOKEN, True, False, id="token-no-turn-open"),
        pytest.param("deliver_push_tool_call", _TOOL, False, True, id="tool-push-disabled"),
        pytest.param("deliver_push_tool_call", _TOOL, True, False, id="tool-no-turn-open"),
    ],
)
async def test_deliver_push_is_rejected_outside_an_allowed_turn(
    make_push_bridge, deliver, payload, push_enabled, stale_open
):
    bridge, _ = make_push_bridge(
        config=ClientConfig(push_enabled=push_enabled, tool_calls_enabled=True)
    )
    if stale_open:
        bridge._push_turn_id = "t-1"
        bridge._push_turn_open = True
    await getattr(bridge, deliver)(payload)
    assert await sent(bridge) == []


async def test_on_push_turn_end_bounds_audio_drain(make_push_bridge, monkeypatch):
    """#130 S3a: la espera de la síntesis TTS está acotada por
    PUSH_TTS_DRAIN_TIMEOUT_S. Al vencer se cancela el audio, se cierra el TTS y
    el turn_end se envía igualmente."""
    from jota_gateway.core.config import settings

    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    bridge, ws = make_push_bridge(output_mode=("audio", "text"))
    mock_tts = AsyncMock()
    bridge._push_tts = mock_tts
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True
    stuck = asyncio.create_task(asyncio.sleep(3600))
    bridge._push_audio_task = stuck

    await asyncio.wait_for(bridge.on_push_turn_end("agent:main:hab_sito"), timeout=2.0)

    assert stuck.cancelled()
    mock_tts.close.assert_awaited_once()
    assert {"type": "turn_end", "turn_id": "t-1"} in await sent(bridge)
    assert bridge._push_turn_open is False
    assert bridge._push_tts is None


# ── #241/#243: cableado de ChatTextTracker en deliver_push (casos del tracker en test_chat_text.py)


def _m(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _final(text):
    return {"state": "final", "message": _m(text)}


def _open(bridge):
    bridge._push_turn_open = True
    bridge._push_turn_id = "t-1"


async def test_push_captured_sequence_with_snapshots(make_push_bridge):
    bridge, ws = make_push_bridge()
    _open(bridge)
    await bridge.deliver_push({"state": "delta", "deltaText": "1, 2, 3,", "message": _m("1, 2, 3,")})
    await bridge.deliver_push(
        {"state": "delta", "deltaText": "", "replace": True, "message": _m("")}
    )
    await bridge.deliver_push(_final("1, 2, 3, 4, 5"))
    assert [m["text"] for m in await sent(bridge)] == ["1, 2, 3,", " 4, 5"]


async def test_push_final_retracted_to_prefix_emits_nothing(make_push_bridge, caplog):
    caplog.set_level(logging.DEBUG)
    bridge, ws = make_push_bridge()
    _open(bridge)
    await bridge.deliver_push({"state": "delta", "deltaText": "SECRETO, mundo. Adios"})
    await bridge.deliver_push(_final("SECRETO, mundo."))
    assert [m["text"] for m in await sent(bridge)] == ["SECRETO, mundo. Adios"]
    assert "diverges" not in caplog.text
    assert "SECRETO" not in caplog.text


# ── 7e: hablar a Jota corta un push que está sonando ──


async def _hang():
    await asyncio.sleep(3600)
    yield b""


async def test_cut_during_push_turn_end_sends_one_turn_end(make_push_bridge):
    """Foco 1: un corte mientras on_push_turn_end espera un audio que no acaba."""
    push_tts = AsyncMock()
    push_tts.get_audio_stream = _hang
    tts_wrapper = AsyncMock()
    tts_wrapper.connect = AsyncMock(return_value=push_tts)
    bridge, _ = make_push_bridge(output_mode=("audio", "text"), tts=tts_wrapper)
    sk = bridge._session_key()
    await bridge.on_push_turn_start(sk)

    ending = asyncio.create_task(bridge.on_push_turn_end(sk))
    await wait_until(lambda: push_tts.end.await_count == 1)
    await asyncio.sleep(0.02)  # ya espera al audio
    assert await bridge._cut_push() == "t-1"
    await asyncio.wait_for(ending, 2)

    assert [m for m in await sent(bridge) if m.get("type") == "turn_end"] == [
        {"type": "turn_end", "turn_id": "t-1"}
    ]
    bridge.orchestrator.abort.assert_awaited_once_with(sk)
    assert bridge._push_turn_open is False


async def test_cut_while_push_tts_connects_closes_that_tts(make_push_bridge):
    """Un corte durante el connect del TTS del push: ese TTS se cierra, sin audio huérfano."""
    push_tts = AsyncMock()
    push_tts.get_audio_stream = _hang
    go = asyncio.Event()

    async def _connect(**_):
        await go.wait()
        return push_tts

    tts_wrapper = AsyncMock()
    tts_wrapper.connect = AsyncMock(side_effect=_connect)
    bridge, _ = make_push_bridge(output_mode=("audio", "text"), tts=tts_wrapper)
    opening = asyncio.create_task(bridge.on_push_turn_start(bridge._session_key()))
    await wait_until(lambda: tts_wrapper.connect.await_count == 1)
    assert await bridge._cut_push() == "t-1"
    go.set()
    await asyncio.wait_for(opening, 2)

    push_tts.close.assert_awaited_once()
    assert bridge._push_tts is None and bridge._push_audio_task is None
    assert [m["type"] for m in await sent(bridge)] == ["turn_start", "turn_end"]


async def test_cut_push_with_another_expected_id_is_a_noop(make_push_bridge):
    """Barge-in/cancel only cut the push that was open when they arrived (I-1)."""
    bridge, _ = make_push_bridge(output_mode=("text",))
    await bridge.on_push_turn_start(bridge._session_key())
    assert await bridge._cut_push("t-9") is None
    assert bridge._push_turn_open is True
    bridge.orchestrator.abort.assert_not_awaited()


async def test_close_all_while_push_worker_waits_on_turn_lock_ends_promptly(make_push_bridge):
    bridge, _ = make_push_bridge(output_mode=("text",))
    await bridge._turn_lock.acquire()  # a turn being replaced holds the lock
    bridge.enqueue_push("turn_start", bridge._session_key())
    await asyncio.sleep(0.02)
    await asyncio.wait_for(bridge.close_all(), 2)
    assert bridge._push_worker.done()
    assert [m for m in await sent(bridge) if m.get("type") == "turn_start"] == []
