"""Tests for OutboundSender implementations (issue #130 S1)."""

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from src.core.config import settings
from src.services.outbound import ClientGone, ClientSlow, DirectSender, QueuedSender


class FakeWS:
    """Records what reaches the socket; optionally fails or hangs."""

    def __init__(self, fail_on: int | None = None, hang: bool = False):
        self.sent: list[tuple[str, object]] = []
        self._fail_on = fail_on  # 1-based index of the send that raises
        self._hang = hang
        self._calls = 0

    async def _send(self, kind: str, payload):
        self._calls += 1
        if self._hang:
            await asyncio.Event().wait()
        await asyncio.sleep(0)  # let other tasks interleave
        if self._fail_on is not None and self._calls == self._fail_on:
            raise RuntimeError("socket roto")
        self.sent.append((kind, payload))

    async def send_json(self, payload):
        await self._send("json", payload)

    async def send_bytes(self, data):
        await self._send("bytes", data)


async def test_direct_sender_forwards_to_ws():
    ws = FakeWS()
    s = DirectSender(ws)
    await s.send_json({"a": 1})
    await s.send_bytes(b"x")
    await s.flush()
    await s.aclose(1.0)
    assert ws.sent == [("json", {"a": 1}), ("bytes", b"x")]


async def test_direct_sender_propagates_ws_errors():
    s = DirectSender(FakeWS(fail_on=1))
    with pytest.raises(RuntimeError):
        await s.send_json({"a": 1})


async def test_queued_preserves_enqueue_order_across_concurrent_tasks():
    ws = FakeWS()
    s = QueuedSender(ws)
    order: list[int] = []
    counter = iter(range(1000))

    async def producer():
        for _ in range(20):
            n = next(counter)
            order.append(n)  # no await between this and the enqueue below
            await s.send_json({"n": n})
            await asyncio.sleep(0)

    await asyncio.gather(*(producer() for _ in range(5)))
    await s.flush()
    await s.aclose(1.0)
    assert [p["n"] for _, p in ws.sent] == order


async def test_queued_interleaves_json_and_bytes_in_enqueue_order():
    ws = FakeWS()
    s = QueuedSender(ws)
    await s.send_json({"type": "turn_start"})
    await s.send_bytes(b"\xa1a")
    await s.send_json({"type": "token"})
    await s.send_bytes(b"\xa1b")
    await s.send_json({"type": "turn_end"})
    await s.aclose(1.0)
    assert [(k, p if k == "bytes" else p["type"]) for k, p in ws.sent] == [
        ("json", "turn_start"),
        ("bytes", b"\xa1a"),
        ("json", "token"),
        ("bytes", b"\xa1b"),
        ("json", "turn_end"),
    ]


async def test_queued_send_returns_before_socket_write():
    ws = FakeWS()
    s = QueuedSender(ws)
    await s.send_json({"a": 1})
    assert ws.sent == []  # still queued; the writer hasn't run yet
    await s.flush()
    assert ws.sent == [("json", {"a": 1})]
    await s.aclose(1.0)


async def test_queued_failure_marks_broken_and_next_send_raises_client_gone():
    s = QueuedSender(FakeWS(fail_on=1))
    await s.send_json({"a": 1})  # accepted: the failure is only known later
    await s._writer  # the writer exits after the failure
    with pytest.raises(ClientGone):
        await s.send_json({"b": 2})
    with pytest.raises(ClientGone):
        await s.send_bytes(b"x")


async def test_queued_failure_discards_pending_messages():
    ws = FakeWS(fail_on=1)
    s = QueuedSender(ws)
    for i in range(5):
        await s.send_json({"n": i})
    await s._writer
    assert ws.sent == []


async def test_queued_flush_reraises_failure():
    s = QueuedSender(FakeWS(fail_on=1))
    await s.send_json({"a": 1})
    with pytest.raises(ClientGone):
        await s.flush()


async def test_queued_flush_on_already_broken_sender_raises():
    s = QueuedSender(FakeWS(fail_on=1))
    await s.send_json({"a": 1})
    await s._writer
    with pytest.raises(ClientGone):
        await s.flush()


async def test_queued_failure_logs_single_warning_without_payload(caplog):
    s = QueuedSender(FakeWS(fail_on=1))
    with caplog.at_level("WARNING", logger="src.services.outbound"):
        await s.send_json({"type": "transcription", "text": "SECRETO"})
        await s.send_json({"n": 2})
        await s._writer
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "SECRETO" not in warnings[0].getMessage()


async def test_queued_aclose_drains_pending_messages():
    ws = FakeWS()
    s = QueuedSender(ws)
    for i in range(10):
        await s.send_json({"n": i})
    await s.aclose(1.0)
    assert [p["n"] for _, p in ws.sent] == list(range(10))


async def test_queued_send_after_aclose_raises_client_gone():
    s = QueuedSender(FakeWS())
    await s.aclose(1.0)
    with pytest.raises(ClientGone):
        await s.send_json({"a": 1})
    with pytest.raises(ClientGone):
        await s.flush()


async def test_queued_aclose_is_idempotent():
    s = QueuedSender(FakeWS())
    await s.aclose(1.0)
    await s.aclose(1.0)


async def test_queued_aclose_respects_timeout_when_socket_hangs():
    s = QueuedSender(FakeWS(hang=True))
    await s.send_json({"a": 1})
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await s.aclose(0.05)  # must not raise
    assert loop.time() - t0 < 1.0
    assert s._writer.done()


async def test_queued_aclose_does_not_wait_when_socket_broken():
    s = QueuedSender(FakeWS(fail_on=1))
    await s.send_json({"a": 1})
    await s._writer
    await asyncio.wait_for(s.aclose(30.0), timeout=1.0)


async def test_queued_aclose_timeout_fails_pending_flush():
    s = QueuedSender(FakeWS(hang=True))
    await s.send_json({"a": 1})
    flusher = asyncio.create_task(s.flush())
    await asyncio.sleep(0)
    await s.aclose(0.05)
    with pytest.raises(ClientGone):
        await flusher


class RaisingWS:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def send_json(self, payload):
        raise self._exc

    async def send_bytes(self, data):
        raise self._exc


async def _fail_once(exc, caplog):
    s = QueuedSender(RaisingWS(exc))
    with caplog.at_level("INFO", logger="src.services.outbound"):
        await s.send_json({"type": "transcription", "text": "SECRETO"})
        await s._writer
    return [r for r in caplog.records if r.name == "src.services.outbound"]


@pytest.mark.parametrize(
    "exc",
    [
        WebSocketDisconnect(code=1006),
        RuntimeError('Cannot call "send" once a close message has been sent.'),
    ],
)
async def test_queued_routine_disconnect_logs_info_not_warning(caplog, exc):
    records = await _fail_once(exc, caplog)
    assert [r.levelname for r in records] == ["INFO"]
    assert "SECRETO" not in records[0].getMessage()
    assert type(exc).__name__ in records[0].getMessage()


async def test_queued_unexpected_error_logs_warning(caplog):
    records = await _fail_once(RuntimeError("boom SECRETO"), caplog)
    assert [r.levelname for r in records] == ["WARNING"]
    assert "SECRETO" not in records[0].getMessage()


async def test_queued_cancelled_writer_counts_as_closed():
    s = QueuedSender(FakeWS())
    s._writer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await s._writer
    with pytest.raises(ClientGone):
        await s.send_json({"a": 1})
    with pytest.raises(ClientGone):
        await s.flush()


async def test_queued_cancelled_aclose_still_fails_pending_flush():
    s = QueuedSender(FakeWS(hang=True))
    await s.send_json({"a": 1})
    flusher = asyncio.create_task(s.flush())
    await asyncio.sleep(0)
    closer = asyncio.create_task(s.aclose(30.0))
    await asyncio.sleep(0.01)
    closer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closer
    with pytest.raises(ClientGone):
        await asyncio.wait_for(flusher, timeout=1.0)


class DelayedWS(FakeWS):
    """Cada send tarda `delay` pero termina."""

    def __init__(self, delay: float):
        super().__init__()
        self._delay = delay

    async def _send(self, kind: str, payload):
        await asyncio.sleep(self._delay)
        self.sent.append((kind, payload))


async def test_queued_send_blocked_past_timeout_marks_client_slow(monkeypatch):
    monkeypatch.setattr(settings, "CLIENT_SEND_TIMEOUT_S", 0.05)
    calls: list[int] = []

    async def on_slow():
        calls.append(1)

    s = QueuedSender(FakeWS(hang=True), on_slow=on_slow)
    await s.send_json({"type": "token"})
    await asyncio.wait_for(s._writer, timeout=1.0)

    assert isinstance(s._failure, ClientSlow)
    assert calls == [1]
    with pytest.raises(ClientGone) as exc:
        await s.send_json({"x": 1})
    assert isinstance(exc.value.__cause__, ClientSlow)


async def test_queued_slow_client_discards_pending_and_fails_flush(monkeypatch):
    monkeypatch.setattr(settings, "CLIENT_SEND_TIMEOUT_S", 0.05)
    s = QueuedSender(FakeWS(hang=True))
    await s.send_json({"a": 1})
    await s.send_json({"b": 2})
    flush = asyncio.create_task(s.flush())

    with pytest.raises(ClientGone):
        await asyncio.wait_for(flush, timeout=1.0)
    assert s._queue.empty()


async def test_queued_fast_sends_are_unaffected_by_the_timeout(monkeypatch):
    monkeypatch.setattr(settings, "CLIENT_SEND_TIMEOUT_S", 0.5)
    ws = FakeWS()
    s = QueuedSender(ws, on_slow=None)
    for i in range(3):
        await s.send_json({"i": i})
    await s.flush()
    assert s._failure is None
    assert [p for _, p in ws.sent] == [{"i": 0}, {"i": 1}, {"i": 2}]
    await s.aclose(1.0)


async def test_queued_timeout_is_per_send_not_cumulative(monkeypatch):
    """A slow-but-progressing client must not be cut."""
    monkeypatch.setattr(settings, "CLIENT_SEND_TIMEOUT_S", 0.2)
    ws = DelayedWS(delay=0.02)  # each send << timeout; 15 sends total > timeout
    called: list[int] = []

    async def on_slow():
        called.append(1)

    s = QueuedSender(ws, on_slow=on_slow)
    for i in range(15):
        await s.send_json({"i": i})
    await s.flush()

    assert s._failure is None
    assert called == []
    assert len(ws.sent) == 15
    await s.aclose(1.0)


async def test_queued_send_raising_timeouterror_itself_is_not_a_slow_client():
    """TimeoutError is an OSError subclass; only the writer's own
    asyncio.timeout expiring means 'slow client'."""
    called: list[int] = []

    async def on_slow():
        called.append(1)

    s = QueuedSender(RaisingWS(TimeoutError("socket")), on_slow=on_slow)
    await s.send_json({"a": 1})
    await asyncio.wait_for(s._writer, timeout=1.0)

    assert isinstance(s._failure, TimeoutError)
    assert not isinstance(s._failure, ClientSlow)
    assert called == []


async def test_queued_on_slow_raising_does_not_break_teardown(monkeypatch, caplog):
    """A raising on_slow callback is logged and does not break teardown."""
    monkeypatch.setattr(settings, "CLIENT_SEND_TIMEOUT_S", 0.05)

    async def on_slow():
        raise RuntimeError("callback roto")

    s = QueuedSender(FakeWS(hang=True), on_slow=on_slow)
    with caplog.at_level("WARNING", logger="src.services.outbound"):
        await s.send_json({"a": 1})
        await asyncio.wait_for(s._writer, timeout=1.0)  # writer ends, no exception escapes

    assert isinstance(s._failure, ClientSlow)
    assert any("on_slow falló" in r.getMessage() for r in caplog.records)
    await asyncio.wait_for(s.aclose(1.0), timeout=2.0)


async def test_queued_on_slow_hanging_does_not_wedge_aclose(monkeypatch):
    """A hanging callback is cancelled by aclose's own bound."""
    monkeypatch.setattr(settings, "CLIENT_SEND_TIMEOUT_S", 0.05)
    started = asyncio.Event()

    async def on_slow():
        started.set()
        await asyncio.Event().wait()

    s = QueuedSender(FakeWS(hang=True), on_slow=on_slow)
    await s.send_json({"a": 1})
    await asyncio.wait_for(started.wait(), timeout=1.0)

    await asyncio.wait_for(s.aclose(0.1), timeout=2.0)
    assert s._writer.done()


async def test_queued_slow_client_logs_one_warning_without_payload(monkeypatch, caplog):
    monkeypatch.setattr(settings, "CLIENT_SEND_TIMEOUT_S", 0.05)
    s = QueuedSender(FakeWS(hang=True))
    with caplog.at_level("INFO", logger="src.services.outbound"):
        await s.send_json({"type": "transcription", "text": "SECRETO"})
        await asyncio.wait_for(s._writer, timeout=1.0)
    records = [r for r in caplog.records if r.name == "src.services.outbound"]
    assert len(records) == 1
    assert records[0].levelname == "WARNING"
    assert "SECRETO" not in records[0].getMessage()
