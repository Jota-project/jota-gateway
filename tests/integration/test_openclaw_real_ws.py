"""Frontera con OpenClaw contra un servidor `websockets` REAL (issue #213, M6a).

Los dobles de `SmartFakeWS` reprodujeron el diseño del cliente y ocultaron bugs
(G-03/G-04/G-05). Aquí el servidor es un `websockets.serve` en el puerto 0 y el cliente
es el `OpenClawClient` de producción, con dos sesiones concurrentes sobre la misma
conexión multiplexada.
"""

import asyncio
import json
import logging

import pytest

from fogar_gateway.services.openclaw.client import OpenClawClient
from fogar_gateway.services.openclaw.dispatcher import FrameDispatcher
from fogar_gateway.services.openclaw.registry import ClientRegistry, TurnRegistry
from tests.fakes.openclaw import MIB, FakeOpenClawServer

SK_A = "agent:main:client-a"
SK_B = "agent:main:client-b"


def make_client(port):
    turns = TurnRegistry()
    dispatcher = FrameDispatcher(turns, ClientRegistry())
    client = OpenClawClient("127.0.0.1", port, "tok", turns, dispatcher)
    return client, turns


async def run_turn(client, sk, text):
    return [e async for e in client.stream_response(text, session_key=sk)]


def tokens(events):
    return "".join(e.content for e in events if e.type == "token")


def errors(events):
    return [e.content for e in events if e.type == "error"]


@pytest.fixture
async def server():
    async with FakeOpenClawServer() as srv:
        yield srv


async def _two_sessions(server, text_a, text_b, release_after=None):
    """B queda en `hold`; A ejecuta `text_a`. Devuelve (events_a, events_b, client, turns)."""
    client, turns = make_client(server.port)
    await client.connect()
    task_b = asyncio.create_task(run_turn(client, SK_B, text_b))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if turns.get_queue_by_session(SK_B) is not None:
            break
    events_a = await asyncio.wait_for(run_turn(client, SK_A, text_a), 5)
    return events_a, task_b, client, turns


# ── G-03 ──────────────────────────────────────────────────────────


async def test_malformed_frames_do_not_affect_concurrent_session(server):
    events_a, task_b, client, turns = await _two_sessions(server, "malformed", "hold")
    try:
        server.release.set()
        events_b = await asyncio.wait_for(task_b, 5)
        assert errors(events_a) == [] and tokens(events_a) == "reply-malformed"
        assert errors(events_b) == [] and tokens(events_b) == "reply-hold"
        assert server.connections == 1
    finally:
        await client.close()


async def test_malformed_frame_warning_does_not_leak_content(server, caplog):
    caplog.set_level(logging.WARNING)
    events_a, task_b, client, _ = await _two_sessions(server, "malformed", "hold")
    try:
        server.release.set()
        await asyncio.wait_for(task_b, 5)
        assert "this is not json" not in caplog.text
        assert any("malformed" in r.getMessage().lower() for r in caplog.records)
    finally:
        await client.close()


# ── G-04 ──────────────────────────────────────────────────────────


async def test_clean_close_errors_pending_turns_fast_and_frees_session_key(server):
    client, turns = make_client(server.port)
    await client.connect()
    task_b = asyncio.create_task(run_turn(client, SK_B, "hold"))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if turns.get_queue_by_session(SK_B) is not None:
            break
    try:
        events_a = await asyncio.wait_for(run_turn(client, SK_A, "close"), 3)
        events_b = await asyncio.wait_for(task_b, 3)
        assert errors(events_a) == ["orchestrator_disconnected"]
        assert errors(events_b) == ["orchestrator_disconnected"]
        assert turns.get_queue_by_session(SK_B) is None
        assert turns.get_queue_by_session(SK_A) is None
    finally:
        server.release.set()
        await client.close()


async def test_listen_errors_use_stable_message_not_raw_exception():
    turns = TurnRegistry()
    client = OpenClawClient("x", 1, "t", turns, FrameDispatcher(turns, ClientRegistry()))

    class Boom:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("secret internal detail")

    client._ws = Boom()
    q = turns.register("r", SK_A)
    await client._listen()
    assert q.get_nowait() == ("error", "orchestrator_disconnected")


# ── G-05 ──────────────────────────────────────────────────────────


async def test_two_mib_frame_does_not_drop_connection_or_other_session(server):
    events_a, task_b, client, _ = await _two_sessions(server, "big", "hold")
    try:
        server.release.set()
        events_b = await asyncio.wait_for(task_b, 5)
        assert errors(events_a) == [] and len(tokens(events_a)) == 2 * MIB
        assert errors(events_b) == [] and tokens(events_b) == "reply-hold"
        assert server.connections == 1
    finally:
        await client.close()


async def test_frame_larger_than_default_follows_announced_max_payload():
    hello = {
        "type": "hello-ok",
        "protocol": 4,
        "server": {"version": "9", "connId": "c"},
        "policy": {"tickIntervalMs": 30000, "maxPayload": 40 * MIB},
        "snapshot": {"sessionDefaults": {"defaultAgentId": "main"}},
    }
    async with FakeOpenClawServer(hello_payload=hello, big_bytes=30 * MIB) as srv:
        client, _ = make_client(srv.port)
        await client.connect()
        try:
            events = await asyncio.wait_for(run_turn(client, SK_A, "big"), 10)
            assert errors(events) == [] and len(tokens(events)) == 30 * MIB
        finally:
            await client.close()


def test_frame_limit_clamps():
    from fogar_gateway.services.openclaw.client import _frame_limit

    assert _frame_limit(25 * MIB) == 25 * MIB
    assert _frame_limit(10**12) == 64 * MIB
    assert _frame_limit(10) == 1 * MIB
    assert _frame_limit(None) == 25 * MIB
    assert _frame_limit("junk") == 25 * MIB


# ── G-47 ──────────────────────────────────────────────────────────


async def test_ok_true_res_other_than_started_does_not_end_turn(server):
    client, _ = make_client(server.port)
    await client.connect()
    try:
        events = await asyncio.wait_for(run_turn(client, SK_A, "accepted"), 5)
        assert tokens(events) == "reply-accepted"
        assert errors(events) == []
    finally:
        await client.close()


# ── G-48 ──────────────────────────────────────────────────────────


async def test_hello_ok_server_and_protocol_are_logged_once(server, caplog):
    caplog.set_level(logging.INFO, logger="fogar_gateway.services.openclaw.client")
    client, _ = make_client(server.port)
    await client.connect()
    try:
        recs = [r.getMessage() for r in caplog.records if "hello-ok" in r.getMessage()]
        assert len(recs) == 1
        assert "2026.6.11" in recs[0] and "protocol=4" in recs[0]
    finally:
        await client.close()


# ── Observabilidad de la caída (revisión de #226) ─────────────────


async def test_1009_close_logs_code_but_no_frame_content(server, caplog):
    caplog.set_level(logging.DEBUG, logger="fogar_gateway.services.openclaw.client")
    client, _ = make_client(server.port)
    await client.connect()
    try:
        events = await asyncio.wait_for(run_turn(client, SK_A, "huge"), 10)
        assert errors(events) == ["orchestrator_disconnected"]
        assert "1009" in caplog.text
        assert "SECRETCONTENT" not in caplog.text
    finally:
        await client.close()


async def test_handler_error_logs_traceback_without_exception_message(caplog):
    caplog.set_level(logging.DEBUG, logger="fogar_gateway.services.openclaw.client")
    turns = TurnRegistry()
    client = OpenClawClient("x", 1, "t", turns, FrameDispatcher(turns, ClientRegistry()))

    class Boom:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("secret internal detail")

    client._ws = Boom()
    await client._listen()
    assert "secret internal detail" not in caplog.text
    assert "RuntimeError" in caplog.text
    assert "__anext__" in caplog.text  # traceback frames (code lines) are present


# ── Reconexión tras cierre limpio (hueco de cobertura de #226) ────


async def test_clean_close_fires_on_disconnect_after_error_all_and_new_connection_is_clean(server):
    from fogar_gateway.services.openclaw.reconnecting import ReconnectingOpenClawClient
    from fogar_gateway.services.reconnection import ConnectionState

    client, turns = make_client(server.port)
    order = []
    real_error_all = turns.error_all
    turns.error_all = lambda msg: (order.append(("error_all", msg)), real_error_all(msg))[1]
    roc = ReconnectingOpenClawClient(client, "oc", initial_backoff=0.05, max_backoff=0.1)
    real_on_disc = client.on_disconnect
    client.on_disconnect = lambda: (order.append(("on_disconnect", None)), real_on_disc())[1]
    await roc.connect()
    try:
        events = await asyncio.wait_for(run_turn(roc, SK_A, "close"), 3)
        assert errors(events) == ["orchestrator_disconnected"]
        assert order[:2] == [("error_all", "orchestrator_disconnected"), ("on_disconnect", None)]
        for _ in range(100):
            if roc.state == ConnectionState.CONNECTED and server.connections == 2:
                break
            await asyncio.sleep(0.05)
        assert server.connections == 2 and roc.state == ConnectionState.CONNECTED
        calls_before = len(order)
        events = await asyncio.wait_for(run_turn(roc, SK_A, "ok"), 3)
        assert errors(events) == [] and tokens(events) == "reply-ok"
        await asyncio.sleep(0.1)
        assert [o for o in order[calls_before:] if o[0] == "error_all"] == []
        assert [o for o in order if o[0] == "error_all"] == [
            ("error_all", "orchestrator_disconnected")
        ]
    finally:
        await roc.close()


# ── Escenarios del arnés e2e (M7a) ────────────────────────────────


async def test_fail_ends_turn_with_stable_orchestrator_error(server):
    client, _ = make_client(server.port)
    await client.connect()
    try:
        events = await asyncio.wait_for(run_turn(client, SK_A, "fail"), 5)
        assert errors(events) == ["orchestrator_error"] and tokens(events) == ""
    finally:
        await client.close()


async def test_agent_error_after_ack_ends_turn_with_stable_error(server, caplog):
    # #300: el chat state:error termina el turno ya, sin esperar a turn_timeout.
    caplog.set_level(logging.DEBUG)
    client, turns = make_client(server.port)
    await client.connect()
    try:
        events = await asyncio.wait_for(run_turn(client, SK_A, "agent_error"), 3)
        assert errors(events) == ["orchestrator_error"] and tokens(events) == ""
        assert turns.get_queue_by_session(SK_A) is None
        assert "No API key" not in caplog.text
    finally:
        await client.close()


async def test_drop_mid_turn_delivers_delta_then_disconnect_error(server):
    client, turns = make_client(server.port)
    await client.connect()
    try:
        events = await asyncio.wait_for(run_turn(client, SK_A, "drop"), 5)
        assert tokens(events) == "reply-drop"
        assert errors(events) == ["orchestrator_disconnected"]
        assert turns.get_queue_by_session(SK_A) is None
    finally:
        await client.close()


async def test_push_reaches_registered_bridge_in_order(server):
    class FakeBridge:
        def __init__(self):
            self.calls = []

        def enqueue_push(self, kind, payload):
            self.calls.append((kind, payload))

    client, _ = make_client(server.port)
    bridge = FakeBridge()
    client._dispatcher._clients.register("client-a", bridge)
    await client.connect()
    try:
        await server.push(SK_A, "hola")
        for _ in range(100):
            if len(bridge.calls) >= 4:
                break
            await asyncio.sleep(0.02)
        assert [k for k, _ in bridge.calls] == ["turn_start", "chat", "chat", "turn_end"]
        assert bridge.calls[1][1]["deltaText"] == "hola" and bridge.calls[2][1]["state"] == "final"
    finally:
        await client.close()


async def test_push_without_open_connection_raises(server):
    with pytest.raises(RuntimeError):
        await server.push(SK_A, "x")


# ── Cableado de ChatTextTracker en `stream_response` (tramas de producción) ──
# Los casos del tracker viven en tests/unit/test_chat_text.py.

TEXT = "SECRETREPLY"


def _msg(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}], "timestamp": 1}


def _ev(sk, **kw):
    return json.dumps({"type": "event", "event": "chat", "payload": {"sessionKey": sk, **kw}})


def _delta(sk, text, seq, replace=False):
    kw = {"state": "delta", "seq": seq, "deltaText": text, "message": _msg(text)}
    if replace:
        kw["replace"] = True
    return _ev(sk, **kw)


def _final(sk, text, seq=11):
    return _ev(sk, state="final", seq=seq, stopReason="stop", message=_msg(text))


class Scripted2026_10_5(FakeOpenClawServer):
    async def _chat_send(self, ws, rid, params):
        sk, scenario = params["sessionKey"], params["message"]
        await ws.send(self._res(rid, {"status": "started"}))
        if scenario == "captured":
            for phase, seq in (
                ("preparing_workspace", 1),
                ("preparing_context", 2),
                ("starting_model", 4),
            ):
                await ws.send(_ev(sk, state="status", phase=phase, seq=seq))
            await ws.send(_delta(sk, "1, 2, 3,", 6))
            await ws.send(_delta(sk, "", 9, replace=True))
            await ws.send(_final(sk, "1, 2, 3, 4, 5"))
        elif scenario == "multi_message":
            await ws.send(_delta(sk, "Voy a buscar. ", 1))
            await ws.send(_final(sk, f"Resultado: {TEXT}"))


@pytest.fixture
async def scripted_server():
    async with Scripted2026_10_5() as srv:
        yield srv


async def _turn(server, scenario):
    client, _ = make_client(server.port)
    await client.connect()
    try:
        return await run_turn(client, SK_A, scenario)
    finally:
        await client.close()


def _toks(events):
    return [e.content for e in events if e.type == "token"]


async def test_captured_sequence_delivers_full_text_then_done(scripted_server):
    events = await _turn(scripted_server, "captured")
    assert _toks(events) == ["1, 2, 3,", " 4, 5"]
    assert events[-1].type == "status" and events[-1].content == "done"
    assert errors(events) == []


async def test_diverging_final_emits_full_final_and_warns_without_content(scripted_server, caplog):
    caplog.set_level(logging.DEBUG)
    events = await _turn(scripted_server, "multi_message")
    assert _toks(events) == ["Voy a buscar. ", f"Resultado: {TEXT}"]
    assert events[-1].content == "done"
    assert any("diverges" in r.getMessage() for r in caplog.records)
    assert TEXT not in caplog.text
    assert "Voy a buscar" not in caplog.text


# ── 7e: chat.abort se queda con su propio `aborted` (G-14, sin filtro por runId) ──


async def test_abort_absorbs_its_aborted_frame(server):
    class FakeBridge:
        def __init__(self):
            self.calls = []

        def enqueue_push(self, kind, payload):
            self.calls.append((kind, payload))

    client, turns = make_client(server.port)
    bridge = FakeBridge()
    client._dispatcher._clients.register("client-a", bridge)
    await client.connect()
    try:
        await server.push(SK_A, "aviso", end=False)
        for _ in range(100):
            if len(bridge.calls) >= 2:
                break
            await asyncio.sleep(0.02)
        await asyncio.wait_for(client.abort(SK_A), 2)

        assert turns.get_queue_by_session(SK_A) is None
        assert not [p for k, p in bridge.calls if k == "chat" and p.get("state") == "aborted"]
        events = await asyncio.wait_for(run_turn(client, SK_A, "hola"), 5)
        assert tokens(events) == "reply-hola" and not errors(events)
    finally:
        await client.close()
