from unittest.mock import AsyncMock

import pytest

from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry


def make_bridge(output_mode=("text",), client_id="hab_sito", tts=None):
    client = Client(id=client_id, client_key="key-123", is_active=True)
    config = ClientConfig()
    ws = AsyncMock()
    orchestrator = AsyncMock()
    orchestrator.ping = AsyncMock(return_value=True)
    tracker = AsyncMock()
    handshake = Handshake(
        client_key="key-123",
        input_mode="text",
        output_mode=list(output_mode),
        agent="main",
    )
    client_registry = ClientRegistry()
    if tts is None:
        tts = AsyncMock()
        tts.connect = AsyncMock(return_value=None)
    bridge = JotaBridge(
        client=client,
        config=config,
        client_ws=ws,
        orchestrator=orchestrator,
        tts=tts,
        tracker=tracker,
        handshake=handshake,
        client_registry=client_registry,
        default_agent="main",
    )
    return bridge, client_registry


@pytest.mark.asyncio
async def test_connect_registers_in_client_registry():
    bridge, registry = make_bridge()
    await bridge.connect_internal_services()
    assert registry.get("hab_sito") is bridge


@pytest.mark.asyncio
async def test_close_unregisters_from_client_registry():
    bridge, registry = make_bridge()
    await bridge.connect_internal_services()
    bridge.tracker.close = AsyncMock()
    await bridge.close_all()
    assert registry.get("hab_sito") is None


@pytest.mark.asyncio
async def test_deliver_push_text_sends_push_message():
    bridge, _ = make_bridge(output_mode=("text",))
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": "Buenos días!"}
    await bridge.deliver_push(payload)
    bridge.client_ws.send_json.assert_awaited_once_with(
        {
            "type": "token",
            "turn_id": "t-1",
            "text": "Buenos días!",
        }
    )


@pytest.mark.asyncio
async def test_deliver_push_empty_delta_ignored():
    bridge, _ = make_bridge(output_mode=("text",))
    bridge._push_turn_open = True
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": ""}
    await bridge.deliver_push(payload)
    bridge.client_ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_push_turn_start_no_audio_does_nothing():
    bridge, _ = make_bridge(output_mode=("text",))
    await bridge.on_push_turn_start("agent:main:hab_sito")
    assert bridge._push_tts is None


@pytest.mark.asyncio
async def test_on_push_turn_end_closes_push_tts():
    bridge, _ = make_bridge(output_mode=("audio", "text"))
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


@pytest.mark.asyncio
async def test_on_push_turn_start_audio_creates_tts_and_pipe():
    mock_tts_client = AsyncMock()
    mock_tts_client.get_audio_stream = lambda: aiter([])
    tts_wrapper = AsyncMock()
    tts_wrapper.connect = AsyncMock(return_value=mock_tts_client)

    bridge, _ = make_bridge(output_mode=("audio", "text"), tts=tts_wrapper)
    await bridge.on_push_turn_start("agent:main:hab_sito")

    tts_wrapper.connect.assert_awaited_once()
    assert bridge._push_tts is mock_tts_client
    assert bridge._push_audio_task is not None


@pytest.mark.asyncio
async def test_push_audio_pipe_forwards_chunks_with_header():
    """Audio chunks from TTS include [0xA1][turn_seq uint16 BE] header."""
    import asyncio

    chunks = [b"\x00\x01", b"\x02\x03"]
    mock_tts_client = AsyncMock()
    mock_tts_client.get_audio_stream = lambda: aiter(chunks)
    tts_wrapper = AsyncMock()
    tts_wrapper.connect = AsyncMock(return_value=mock_tts_client)

    bridge, _ = make_bridge(output_mode=("audio", "text"), tts=tts_wrapper)
    await bridge.on_push_turn_start("agent:main:hab_sito")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    calls = [c.args[0] for c in bridge.client_ws.send_bytes.await_args_list]
    expected_header = bytes([0xA1]) + (1).to_bytes(2, "big")
    assert calls == [expected_header + b"\x00\x01", expected_header + b"\x02\x03"]


@pytest.mark.asyncio
async def test_on_push_turn_start_sends_turn_start_message():
    """on_push_turn_start sends turn_start JSON message to client."""
    bridge, _ = make_bridge(output_mode=("text",))
    await bridge.on_push_turn_start("agent:main:hab_sito")
    bridge.client_ws.send_json.assert_awaited_once_with(
        {
            "type": "turn_start",
            "turn_id": "t-1",
            "turn_seq": 1,
        }
    )


@pytest.mark.asyncio
async def test_on_push_turn_end_sends_turn_end_message():
    """on_push_turn_end sends turn_end JSON message to client."""
    bridge, _ = make_bridge(output_mode=("text",))
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = (
        True  # a turn_end is only valid if a turn_start came before (issue #84)
    )
    await bridge.on_push_turn_end("agent:main:hab_sito")
    bridge.client_ws.send_json.assert_awaited_once_with(
        {
            "type": "turn_end",
            "turn_id": "t-1",
        }
    )


@pytest.mark.asyncio
async def test_deliver_push_with_tts_sends_to_tts():
    bridge, _ = make_bridge(output_mode=("audio", "text"))
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True
    mock_tts = AsyncMock()
    bridge._push_tts = mock_tts
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": "Hola!"}
    await bridge.deliver_push(payload)
    mock_tts.send_text_chunk.assert_awaited_once_with("Hola!")


@pytest.mark.asyncio
async def test_on_push_turn_end_no_tts_is_noop():
    bridge, _ = make_bridge()
    assert bridge._push_tts is None
    await bridge.on_push_turn_end("agent:main:hab_sito")  # must not raise
    assert bridge._push_tts is None


@pytest.mark.asyncio
async def test_push_disabled_ignores_push_turn_start():
    """push_enabled=False hace que on_push_turn_start no haga nada."""
    client = Client(id="ha", client_key="ha-key", is_active=True)
    config = ClientConfig(push_enabled=False)
    ws = AsyncMock()
    handshake = Handshake(client_key="ha-key", input_mode="text", output_mode=["text"])
    bridge = JotaBridge(
        client=client,
        config=config,
        client_ws=ws,
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=AsyncMock(),
        handshake=handshake,
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    await bridge.on_push_turn_start("agent:main:ha")
    ws.send_json.assert_not_awaited()
    assert bridge._push_turn_id is None


@pytest.mark.asyncio
async def test_deliver_push_tool_call_forwarded_when_enabled():
    client = Client(id="hab_sito", client_key="key-123", is_active=True)
    config = ClientConfig(tool_calls_enabled=True)
    ws = AsyncMock()
    handshake = Handshake(
        client_key="key-123", input_mode="text", output_mode=["text"], agent="main"
    )
    bridge = JotaBridge(
        client=client,
        config=config,
        client_ws=ws,
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=AsyncMock(),
        handshake=handshake,
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True
    data = {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {"command": "ls"}}
    await bridge.deliver_push_tool_call(data)
    ws.send_json.assert_awaited_once_with(
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
    )


@pytest.mark.asyncio
async def test_deliver_push_tool_call_not_forwarded_when_disabled():
    client = Client(id="hab_sito", client_key="key-123", is_active=True)
    config = ClientConfig(tool_calls_enabled=False)
    ws = AsyncMock()
    handshake = Handshake(
        client_key="key-123", input_mode="text", output_mode=["text"], agent="main"
    )
    bridge = JotaBridge(
        client=client,
        config=config,
        client_ws=ws,
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=AsyncMock(),
        handshake=handshake,
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True
    data = {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {}}
    await bridge.deliver_push_tool_call(data)
    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_push_tool_call_malformed_payload_ignored():
    client = Client(id="hab_sito", client_key="key-123", is_active=True)
    config = ClientConfig(tool_calls_enabled=True)
    ws = AsyncMock()
    handshake = Handshake(
        client_key="key-123", input_mode="text", output_mode=["text"], agent="main"
    )
    bridge = JotaBridge(
        client=client,
        config=config,
        client_ws=ws,
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=AsyncMock(),
        handshake=handshake,
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True
    await bridge.deliver_push_tool_call({"phase": "update"})  # must not raise
    ws.send_json.assert_not_awaited()


def _send_json_turn_frames(ws_mock):
    """Return only the frames with type turn_start/turn_end the bridge sent."""
    return [
        c.args[0]
        for c in ws_mock.send_json.await_args_list
        if c.args[0].get("type") in ("turn_start", "turn_end")
    ]


@pytest.mark.asyncio
async def test_multiple_push_starts_and_ends_collapse_to_single_pair():
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
    bridge, _ = make_bridge(output_mode=("text",))
    ws = bridge.client_ws

    # The exact sequence from the issue log: 4 starts in a row, then 3 ends.
    await bridge.on_push_turn_start("agent:main:hab_sito")
    await bridge.on_push_turn_start("agent:main:hab_sito")
    await bridge.on_push_turn_start("agent:main:hab_sito")
    await bridge.on_push_turn_start("agent:main:hab_sito")
    await bridge.on_push_turn_end("agent:main:hab_sito")
    await bridge.on_push_turn_end("agent:main:hab_sito")
    await bridge.on_push_turn_end("agent:main:hab_sito")

    frames = _send_json_turn_frames(ws)
    starts = [f for f in frames if f["type"] == "turn_start"]
    ends = [f for f in frames if f["type"] == "turn_end"]

    assert len(starts) == 1, f"expected 1 turn_start, got {len(starts)}: {starts}"
    assert len(ends) == 1, f"expected 1 turn_end, got {len(ends)}: {ends}"
    assert starts[0]["turn_id"] == ends[0]["turn_id"] == "t-1", (
        f"all frames must share turn_id t-1, got: {frames}"
    )


@pytest.mark.asyncio
async def test_first_push_start_assigns_turn_seq_subsequent_does_not():
    """Issue #84 — only the FIRST agent start opens a logical turn and
    consumes a turn_seq. Subsequent agent starts (intermediate tool steps)
    must not increment _turn_seq further, so the client sees one stable
    turn_id for the whole multi-step reply.
    """
    bridge, _ = make_bridge(output_mode=("text",))
    assert bridge._turn_seq == 0

    await bridge.on_push_turn_start("agent:main:hab_sito")
    assert bridge._turn_seq == 1
    assert bridge._push_turn_id == "t-1"

    await bridge.on_push_turn_start("agent:main:hab_sito")
    await bridge.on_push_turn_start("agent:main:hab_sito")

    # Wire-level check: after 3 starts, the client must see exactly one turn_start.
    # Strengthens the test — without the fix, three start frames would be sent.
    starts = [
        c.args[0]
        for c in bridge.client_ws.send_json.await_args_list
        if c.args[0].get("type") == "turn_start"
    ]
    assert len(starts) == 1, f"expected 1 turn_start after 3 starts, got {len(starts)}: {starts}"

    assert bridge._turn_seq == 1, (
        f"only the first agent start should increment _turn_seq; "
        f"after 3 starts it is {bridge._turn_seq}"
    )
    assert bridge._push_turn_id == "t-1"


@pytest.mark.asyncio
async def test_on_push_turn_end_with_no_open_turn_does_not_emit():
    """Issue #84 — an agent end arriving without a matching agent start
    (orphan, or after close_all reset) must not emit a turn_end frame to
    the client. Guards the early-return branch added to on_push_turn_end.
    """
    bridge, _ = make_bridge(output_mode=("text",))
    assert bridge._push_turn_open is False
    bridge._push_turn_id = "t-1"  # simulate stale state from before reset

    await bridge.on_push_turn_end("agent:main:hab_sito")

    assert bridge.client_ws.send_json.await_count == 0, (
        "on_push_turn_end with no open turn must not emit any frame"
    )
    # Flag must stay False (we never set it True).
    assert bridge._push_turn_open is False


@pytest.mark.asyncio
async def test_deliver_push_rejected_when_push_disabled():
    """#109: push_enabled=False must block deliver_push even if _push_turn_open
    is (incorrectly, or from stale state) True."""
    client = Client(id="hab_sito", client_key="key-123", is_active=True)
    config = ClientConfig(push_enabled=False)
    ws = AsyncMock()
    handshake = Handshake(
        client_key="key-123", input_mode="text", output_mode=["text"], agent="main"
    )
    bridge = JotaBridge(
        client=client,
        config=config,
        client_ws=ws,
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=AsyncMock(),
        handshake=handshake,
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True  # simulates stale/inconsistent state — must still be blocked
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": "Buenos días!"}
    await bridge.deliver_push(payload)
    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_push_rejected_when_no_turn_open():
    """#109 core bug: push_enabled=True but no turn_start was ever sent
    (on_push_turn_start never ran) — deliver_push must not leak a token
    frame with a stale/None turn_id outside any turn_start/turn_end envelope."""
    bridge, _ = make_bridge(output_mode=("text",))
    assert bridge._push_turn_open is False
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": "Buenos días!"}
    await bridge.deliver_push(payload)
    bridge.client_ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_push_tool_call_rejected_when_push_disabled():
    client = Client(id="hab_sito", client_key="key-123", is_active=True)
    config = ClientConfig(push_enabled=False, tool_calls_enabled=True)
    ws = AsyncMock()
    handshake = Handshake(
        client_key="key-123", input_mode="text", output_mode=["text"], agent="main"
    )
    bridge = JotaBridge(
        client=client,
        config=config,
        client_ws=ws,
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=AsyncMock(),
        handshake=handshake,
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    bridge._push_turn_id = "t-1"
    bridge._push_turn_open = True
    data = {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {"command": "ls"}}
    await bridge.deliver_push_tool_call(data)
    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_push_tool_call_rejected_when_no_turn_open():
    client = Client(id="hab_sito", client_key="key-123", is_active=True)
    config = ClientConfig(tool_calls_enabled=True)  # push_enabled defaults True
    ws = AsyncMock()
    handshake = Handshake(
        client_key="key-123", input_mode="text", output_mode=["text"], agent="main"
    )
    bridge = JotaBridge(
        client=client,
        config=config,
        client_ws=ws,
        orchestrator=AsyncMock(),
        tts=AsyncMock(),
        tracker=AsyncMock(),
        handshake=handshake,
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    assert bridge._push_turn_open is False
    data = {"phase": "start", "name": "exec", "toolCallId": "call-1", "args": {"command": "ls"}}
    await bridge.deliver_push_tool_call(data)
    ws.send_json.assert_not_awaited()
