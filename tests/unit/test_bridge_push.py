"""Turnos push (iniciados por OpenClaw), vistos desde el cliente.

Los eventos entran como lo hace FrameDispatcher (`enqueue_push`) y se asierta lo que recibe
el cliente (`sent(bridge)`).
"""

import asyncio
import functools
import logging
from unittest.mock import AsyncMock

import pytest

from fogar_gateway.models.schemas import ClientConfig
from fogar_gateway.services.openclaw.registry import ClientRegistry, TurnRegistry
from tests.fakes.bridge import push, push_reply, sent, wait_until

SK = "agent:main:hab_sito"


@pytest.fixture
def make_push_bridge(make_bridge):
    return functools.partial(make_bridge, client_id="hab_sito")


def _tts(chunks=(), *, hang_audio=False):
    """A TTS provider whose stream yields `chunks` (or never ends) and records calls."""
    stream = AsyncMock()

    async def audio():
        for c in chunks:
            yield c
        if hang_audio:
            await asyncio.sleep(3600)

    stream.get_audio_stream = audio
    provider = AsyncMock()
    provider.connect = AsyncMock(return_value=stream)
    provider.resolve_voice = lambda v: v
    return provider, stream


def _turn_frames(msgs):
    return [m for m in msgs if isinstance(m, dict) and m["type"] in ("turn_start", "turn_end")]


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


async def test_text_push_is_one_turn_with_its_tokens(make_push_bridge):
    bridge, _ = make_push_bridge(output_mode=("text",))
    await push(bridge, *push_reply("Buenos días!", sk=SK))
    assert await sent(bridge) == [
        {"type": "turn_start", "turn_id": "t-1", "turn_seq": 1},
        {"type": "token", "turn_id": "t-1", "text": "Buenos días!"},
        {"type": "turn_end", "turn_id": "t-1"},
    ]


async def test_empty_delta_sends_no_token(make_push_bridge):
    bridge, _ = make_push_bridge(output_mode=("text",))
    await push(bridge, *push_reply("", sk=SK))
    assert [m["type"] for m in await sent(bridge)] == ["turn_start", "turn_end"]


async def test_text_only_push_opens_no_tts(make_push_bridge):
    provider, _ = _tts()
    bridge, _ = make_push_bridge(output_mode=("text",), tts=provider)
    await push(bridge, *push_reply("hola", sk=SK))
    provider.connect.assert_not_awaited()


async def test_audio_push_feeds_ends_and_closes_its_tts(make_push_bridge):
    provider, stream = _tts()
    bridge, _ = make_push_bridge(output_mode=("audio", "text"), tts=provider)
    await push(bridge, *push_reply("Hola!", sk=SK))

    provider.connect.assert_awaited_once()
    stream.send_text_chunk.assert_awaited_once_with("Hola!")
    stream.end.assert_awaited_once()
    stream.close.assert_awaited_once()
    assert [m["type"] for m in _turn_frames(await sent(bridge))] == ["turn_start", "turn_end"]


async def test_push_audio_carries_the_turn_seq_header(make_push_bridge):
    """Audio chunks from TTS include [0xA1][turn_seq uint16 BE] header."""
    provider, _ = _tts([b"\x00\x01", b"\x02\x03"])
    bridge, ws = make_push_bridge(output_mode=("audio", "text"), tts=provider)
    await push(bridge, *push_reply("hola", sk=SK))
    audio = [m for m in await sent(bridge) if isinstance(m, bytes)]
    expected_header = bytes([0xA1]) + (1).to_bytes(2, "big")
    assert audio == [expected_header + b"\x00\x01", expected_header + b"\x02\x03"]
    # every audio frame comes before its turn_end
    assert ws.sent[-1] == {"type": "turn_end", "turn_id": "t-1"}


async def test_push_disabled_client_receives_nothing(make_push_bridge):
    provider, _ = _tts()
    bridge, _ = make_push_bridge(
        output_mode=("audio", "text"), tts=provider, config=ClientConfig(push_enabled=False)
    )
    await push(bridge, *push_reply("hola", sk=SK), ("tool", {"phase": "start", "name": "x"}))
    assert await sent(bridge) == []
    provider.connect.assert_not_awaited()


_TOOL = {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {"command": "ls"}}


async def test_push_tool_call_forwarded_when_enabled(make_push_bridge):
    bridge, _ = make_push_bridge(config=ClientConfig(tool_calls_enabled=True))
    await push(bridge, ("turn_start", SK), ("tool", _TOOL))
    assert (await sent(bridge))[1] == {
        "type": "tool_call",
        "turn_id": "t-1",
        "phase": "start",
        "name": "exec",
        "tool_call_id": "call-1",
        "args": {"command": "ls"},
        "result": None,
        "is_error": None,
    }
    await bridge.close_all()


async def test_push_tool_call_not_forwarded_when_disabled(make_push_bridge):
    bridge, _ = make_push_bridge(config=ClientConfig(tool_calls_enabled=False))
    await push(bridge, ("turn_start", SK), ("tool", _TOOL))
    assert [m["type"] for m in await sent(bridge)] == ["turn_start"]
    await bridge.close_all()


async def test_push_tool_call_malformed_payload_ignored(make_push_bridge):
    bridge, _ = make_push_bridge(config=ClientConfig(tool_calls_enabled=True))
    await push(bridge, ("turn_start", SK), ("tool", {"phase": "update"}))  # must not raise
    assert [m["type"] for m in await sent(bridge)] == ["turn_start"]
    await bridge.close_all()


async def test_multiple_push_starts_and_ends_collapse_to_single_pair(make_push_bridge):
    """Reproduces issue #84 verbatim from the voice client log:

    ```
    turn_start t-1 (seq=1)
    turn_start t-2 (seq=2)
    turn_start t-3 (seq=3)
    turn_start t-4 (seq=4)
    turn_end   t-4 (×3)
    ```

    OpenClaw emits N agent starts back-to-back without intermediate agent ends, then a
    cluster of agent ends. Only the first start opens the logical turn (and consumes a
    turn_seq), only the first end closes it; later ends find no open push and are dropped.
    """
    bridge, ws = make_push_bridge(output_mode=("text",))
    await push(bridge, *[("turn_start", SK)] * 4, *[("turn_end", SK)] * 3)

    await sent(bridge)
    assert _turn_frames(ws.sent) == [
        {"type": "turn_start", "turn_id": "t-1", "turn_seq": 1},
        {"type": "turn_end", "turn_id": "t-1"},
    ]
    # the next push is t-2: the intermediate starts consumed no turn_seq
    await push(bridge, ("turn_start", SK))
    assert ws.of("turn_start")[-1]["turn_id"] == "t-2"
    await bridge.close_all()


async def test_orphan_push_end_emits_nothing(make_push_bridge):
    """Issue #84 — an agent end without a matching agent start emits no turn_end."""
    bridge, _ = make_push_bridge(output_mode=("text",))
    await push(bridge, ("turn_end", SK))
    assert await sent(bridge) == []


@pytest.mark.parametrize("kind", ["chat", "tool"])
async def test_push_payload_outside_a_push_turn_is_dropped(make_push_bridge, kind):
    """#109: nothing leaks outside a turn_start/turn_end envelope."""
    bridge, _ = make_push_bridge(config=ClientConfig(tool_calls_enabled=True))
    payload = {"deltaText": "Buenos días!"} if kind == "chat" else _TOOL
    await push(bridge, (kind, payload))
    assert await sent(bridge) == []


async def test_push_end_bounds_audio_drain(make_push_bridge, monkeypatch):
    """#130 S3a: la espera del audio está acotada por PUSH_TTS_DRAIN_TIMEOUT_S sin progreso.
    Al vencer se cancela el audio, se cierra el TTS y el turn_end se envía igualmente."""
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.05)
    provider, stream = _tts(hang_audio=True)
    bridge, _ = make_push_bridge(output_mode=("audio", "text"), tts=provider)

    await asyncio.wait_for(push(bridge, *push_reply("hola", sk=SK)), timeout=2.0)

    stream.close.assert_awaited_once()
    assert _turn_frames(await sent(bridge))[-1] == {"type": "turn_end", "turn_id": "t-1"}


# ── #241/#243: cableado de ChatTextTracker en el push (casos del tracker en test_chat_text.py)


def _m(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _final(text):
    return {"state": "final", "message": _m(text)}


async def _texts(bridge):
    return [m["text"] for m in await sent(bridge) if m["type"] == "token"]


async def test_push_captured_sequence_with_snapshots(make_push_bridge):
    bridge, _ = make_push_bridge()
    await push(
        bridge,
        ("turn_start", SK),
        ("chat", {"state": "delta", "deltaText": "1, 2, 3,", "message": _m("1, 2, 3,")}),
        ("chat", {"state": "delta", "deltaText": "", "replace": True, "message": _m("")}),
        ("chat", _final("1, 2, 3, 4, 5")),
    )
    assert await _texts(bridge) == ["1, 2, 3,", " 4, 5"]
    await bridge.close_all()


async def test_push_final_retracted_to_prefix_emits_nothing(make_push_bridge, caplog):
    caplog.set_level(logging.DEBUG)
    bridge, _ = make_push_bridge()
    await push(
        bridge,
        ("turn_start", SK),
        ("chat", {"state": "delta", "deltaText": "SECRETO, mundo. Adios"}),
        ("chat", _final("SECRETO, mundo.")),
    )
    assert await _texts(bridge) == ["SECRETO, mundo. Adios"]
    assert "diverges" not in caplog.text
    assert "SECRETO" not in caplog.text
    await bridge.close_all()


async def test_each_push_starts_with_fresh_text(make_push_bridge):
    bridge, _ = make_push_bridge()
    await push(bridge, *push_reply("uno", sk=SK), *push_reply("dos", sk=SK))
    assert await _texts(bridge) == ["uno", "dos"]


# ── 7e: hablar a Fogar corta un push que está sonando ──


async def test_cut_while_the_push_drains_its_audio_sends_one_turn_end(make_push_bridge):
    """Foco 1: un corte mientras el fin del push espera un audio que no acaba."""
    provider, stream = _tts(hang_audio=True)
    bridge, _ = make_push_bridge(output_mode=("audio", "text"), tts=provider)
    await push(bridge, ("turn_start", SK))

    bridge.enqueue_push("turn_end", SK)
    await wait_until(lambda: stream.end.await_count == 1)
    await asyncio.sleep(0.02)  # ya espera al audio
    assert await bridge.turns.cancel("cancelled") == (True, "t-1")
    await asyncio.wait_for(bridge.push_idle(), 2)

    assert [m for m in await sent(bridge) if m.get("type") == "turn_end"] == [
        {"type": "turn_end", "turn_id": "t-1"}
    ]
    bridge.orchestrator.abort.assert_awaited_once_with(SK)
    assert bridge.turns.last_ended.end_reason == "cancelled"
    assert bridge.turns.snapshot(0.0)["turn"]["kind"] == "idle"


async def test_cut_while_push_tts_connects_closes_that_tts(make_push_bridge):
    """Un corte durante el connect del TTS del push: ese TTS se cierra, sin audio huérfano."""
    provider, stream = _tts(hang_audio=True)
    go = asyncio.Event()

    async def _connect(**_):
        await go.wait()
        return stream

    provider.connect = AsyncMock(side_effect=_connect)
    bridge, _ = make_push_bridge(output_mode=("audio", "text"), tts=provider)
    bridge.enqueue_push("turn_start", SK)
    await wait_until(lambda: provider.connect.await_count == 1)
    assert await bridge.turns.cancel("cancelled") == (True, "t-1")
    go.set()
    await asyncio.wait_for(bridge.push_idle(), 2)

    stream.close.assert_awaited_once()
    assert [m["type"] for m in await sent(bridge)] == ["turn_start", "turn_end"]
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "push_audio"]


async def test_cancel_cuts_only_the_push_open_when_it_arrived(make_push_bridge):
    """Barge-in/cancel only cut the push that was open when they arrived (I-1): with no
    push open, nothing is aborted and the next push opens normally."""
    bridge, _ = make_push_bridge(output_mode=("text",))
    assert await bridge.turns.cancel("cancelled") == (False, None)
    await push(bridge, ("turn_start", SK))
    assert bridge.turns.snapshot(0.0)["turn"]["kind"] == "push"
    bridge.orchestrator.abort.assert_not_awaited()
    await bridge.close_all()


async def test_queued_events_of_a_cut_push_never_reopen_it(make_push_bridge, monkeypatch):
    """#285: events of the cut push's run still queued for the worker (here behind a slow
    TTS write) are dropped: its nested `agent start` does not come back as a ghost push."""
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.1)
    provider, stream = _tts(hang_audio=True)
    feeding = asyncio.Event()

    async def slow_chunk(_text):
        feeding.set()
        await asyncio.sleep(3600)

    stream.send_text_chunk = slow_chunk
    bridge, ws = make_push_bridge(
        output_mode=("audio", "text"), tts=provider, turn_registry=TurnRegistry()
    )
    for kind, arg in (("turn_start", SK), ("chat", {"deltaText": "a"}), ("turn_start", SK),
                      ("chat", {"deltaText": "b"}), ("turn_end", SK)):  # fmt: skip
        bridge.enqueue_push(kind, arg, run_id="run-1")
    await asyncio.wait_for(feeding.wait(), 2)  # the worker is stuck on "a"

    assert await bridge.turns.cancel("interrupted") == (True, "t-1")
    bridge.enqueue_push("turn_start", SK, run_id="run-2")  # a new run: a new push
    bridge.enqueue_push("chat", {"deltaText": "c"}, run_id="run-2")
    await asyncio.wait_for(bridge.push_idle(), 2)

    msgs = [m for m in await sent(bridge) if isinstance(m, dict)]
    assert [(m["type"], m.get("turn_id"), m.get("text")) for m in msgs] == [
        ("turn_start", "t-1", None),
        ("token", "t-1", "a"),
        ("turn_end", "t-1", None),
        ("turn_start", "t-2", None),
        ("token", "t-2", "c"),
    ]
    await bridge.close_all()


async def test_push_on_another_agent_session_is_cut_without_abort(make_push_bridge):
    """7e: chat.abort only on our own session_key."""
    bridge, _ = make_push_bridge(output_mode=("text",))
    await push(bridge, ("turn_start", "agent:otro:hab_sito"))
    assert await bridge.turns.cancel("cancelled") == (True, "t-1")
    bridge.orchestrator.abort.assert_not_awaited()


async def test_close_all_while_a_push_waits_for_the_user_turn_ends_promptly(make_push_bridge):
    bridge, _ = make_push_bridge(output_mode=("text",))
    await bridge.turns._lock.acquire()  # a turn being replaced holds the lock
    bridge.enqueue_push("turn_start", SK)
    await asyncio.sleep(0.02)
    await asyncio.wait_for(bridge.close_all(), 2)
    assert [m for m in await sent(bridge) if m.get("type") == "turn_start"] == []


async def test_close_all_sends_the_turn_end_of_an_open_push(make_push_bridge):
    provider, stream = _tts(hang_audio=True)
    bridge, _ = make_push_bridge(output_mode=("audio", "text"), tts=provider)
    await push(bridge, *push_reply("hola", sk=SK, end=False))
    await asyncio.wait_for(bridge.close_all(), 2)
    assert _turn_frames(bridge.client_ws.sent)[-1] == {"type": "turn_end", "turn_id": "t-1"}
    stream.close.assert_awaited_once()
    assert bridge.turns.last_ended.end_reason == "closed"
