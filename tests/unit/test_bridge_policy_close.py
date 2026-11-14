"""Cierre por política (1008: revocada o desplazada, M19): una sola vez por sesión, `_released`
siempre se avisa, y desde que empieza no sale nada más que el turn_end (G-18)."""

import asyncio
from unittest.mock import AsyncMock

from fogar_gateway.services.outbound import QueuedSender
from fogar_gateway.services.protocol import OrchestratorEvent
from tests.fakes.bridge import RecWS, blocking, start_turn


class GatedWS(RecWS):
    """Un socket cuya escritura espera a `gate`: así lo encolado sigue en la cola."""

    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()
        self.gate.set()
        self.closed: tuple | None = None

    async def send_json(self, p):
        await self.gate.wait()
        self.sent.append(p)

    async def send_bytes(self, b):
        await self.gate.wait()
        self.sent.append(b)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


async def test_queued_output_is_dropped_once_restricted():
    ws = GatedWS()
    sender = QueuedSender(ws)
    ws.gate.clear()
    await sender.send_bytes(b"\xa1\x00\x01audio")
    await sender.send_json({"type": "token", "turn_id": "t-1", "text": "x"})
    sender.restrict(frozenset({"turn_end"}))
    await sender.send_bytes(b"\xa1\x00\x01more")
    await sender.send_json({"type": "status", "service": "tts", "state": "degraded"})
    await sender.send_json({"type": "turn_end", "turn_id": "t-1", "reason": "closed"})
    ws.gate.set()
    await sender.flush()
    # Lo que aún no había salido tampoco sale: solo el turn_end.
    assert ws.sent == [{"type": "turn_end", "turn_id": "t-1", "reason": "closed"}]
    await sender.aclose(1.0)


async def test_a_revoked_session_sends_only_its_turn_end_then_1008(make_bridge):
    ws = GatedWS()
    bridge, _ = make_bridge(ws)

    async def chatty(*a, **k):
        while True:  # tokens sin parar: lo que siga saliendo después de revocar se vería
            yield OrchestratorEvent(type="token", content="t")
            await asyncio.sleep(0.001)

    bridge.orchestrator.stream_response = chatty
    await start_turn(bridge)
    await asyncio.sleep(0.05)
    restrict = bridge._sender.restrict
    at = {}

    def spy(allowed):
        at["n"] = len(ws.sent)
        restrict(allowed)

    bridge._sender.restrict = spy
    await bridge._revoke_session()
    after = ws.sent[at["n"]:]
    assert [m for m in after if not (isinstance(m, dict) and m["type"] == "turn_end")] == []
    assert after == [{"type": "turn_end", "turn_id": "t-1", "reason": "closed"}]
    assert ws.closed == (1008, "revoked")


async def test_policy_close_runs_once_and_later_callers_wait_for_it(make_bridge):
    ws = GatedWS()
    bridge, _ = make_bridge(ws)
    started = asyncio.Event()
    bridge.orchestrator.stream_response = blocking(started)
    await start_turn(bridge)
    await started.wait()

    revoke = asyncio.create_task(bridge._revoke_session())
    await asyncio.sleep(0)
    await bridge.displace()  # ya cerrándose por revocación: espera a ese, no abre otro
    await revoke
    assert ws.closed == (1008, "revoked")  # el primero gana, motivo incluido
    assert [m["reason"] for m in ws.of("turn_end")] == ["closed"]
    assert bridge.tracker.record.await_args_list.count((("session_revoked",), {})) == 1
    assert (("session_displaced",), {}) not in bridge.tracker.record.await_args_list


async def test_the_displacer_is_released_even_if_recording_fails(make_bridge):
    ws = GatedWS()
    bridge, _ = make_bridge(ws)
    bridge.tracker.record = AsyncMock(side_effect=RuntimeError("tracker roto"))
    await asyncio.wait_for(bridge.displace(), 1.0)  # no espera el tope entero
    assert bridge._released.is_set()
    assert bridge.conversation.released is True
    await asyncio.wait({bridge._policy_close}, timeout=1.0)
    assert ws.closed == (1008, "conversation opened elsewhere")  # y el cierre sigue
