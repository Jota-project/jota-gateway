"""`turn_progress` en `TurnController` (#254): lo que recibe el cliente en los casos límite.

Revisión de la PR: nada tras cancelar un turno (aunque su limpieza tarde), un `toolCallId`
raro no rompe el turno, un cliente sin texto no pierde el progreso por los deltas, ni se
lleva la cuenta de herramientas para quien no lo pidió, y las fases de un push que llegan
antes de su `agent start` salen al abrirlo.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from fogar_gateway.services import turn_progress, turns
from fogar_gateway.services.openclaw.models import ToolCallEvent
from fogar_gateway.services.protocol import OrchestratorEvent
from tests.fakes.bridge import run_turn, sent, start_turn

SK = "agent:main:hab_sito"
WITH_PROGRESS = ("text", "status")


def progress(msgs):
    return [(m["phase"], m["tool"]) for m in msgs if isinstance(m, dict) and m["type"] == "turn_progress"]


def kinds(msgs):
    return [m["type"] for m in msgs if isinstance(m, dict) and m["type"] != "pipeline_event"]


def _tool(phase, name="read", call_id="c1"):
    return OrchestratorEvent(
        type="tool_call", tool_call=ToolCallEvent(phase=phase, name=name, tool_call_id=call_id)
    )


def _stream(*events, then_sleep=0.0):
    async def _s(*a, **k):
        for event in events:
            yield event
        if then_sleep:
            await asyncio.sleep(then_sleep)

    return _s


def _no_tts():
    provider = AsyncMock()
    provider.connect = AsyncMock(return_value=None)  # TTS no disponible: el turno sigue sin audio
    provider.resolve_voice = lambda v: v
    return provider


@pytest.fixture
def interval(monkeypatch):
    def _set(seconds):
        monkeypatch.setattr(turn_progress, "PROGRESS_MIN_INTERVAL_S", seconds)

    return _set


# ── 1. Nada tras cancelar ────────────────────────────────────────────────────


async def test_a_cancelled_turn_sends_no_progress_while_it_cleans_up(make_bridge, interval):
    """La herramienta queda aplazada; el turno se interrumpe y su limpieza dura más que el
    aplazamiento: el progreso aplazado no sale (ni antes ni después de su turn_end)."""
    interval(0.2)
    bridge, _ = make_bridge(output_mode=WITH_PROGRESS)
    started = asyncio.Event()

    async def stream(*a, **k):
        try:
            yield OrchestratorEvent(type="progress", phase="preparing")
            yield _tool("start", "slow_tool")  # aplazado 0,2 s
            started.set()
            await asyncio.sleep(3600)
        finally:
            await asyncio.sleep(0.4)  # limpieza lenta del turno cancelado

    bridge.orchestrator.stream_response = stream
    await start_turn(bridge)
    await asyncio.wait_for(started.wait(), 2)
    await bridge.turns.cancel("interrupted")
    await asyncio.sleep(0.3)
    msgs = await sent(bridge)
    assert progress(msgs) == [("preparing", None)]
    assert kinds(msgs)[-1] == "turn_end"


async def test_no_new_progress_once_the_turn_is_marked_cancelled(make_bridge, interval):
    interval(0)
    bridge, _ = make_bridge(output_mode=WITH_PROGRESS)
    started = asyncio.Event()

    async def stream(*a, **k):
        yield OrchestratorEvent(type="progress", phase="preparing")
        started.set()
        await asyncio.sleep(3600)

    bridge.orchestrator.stream_response = stream
    await start_turn(bridge)
    await asyncio.wait_for(started.wait(), 2)
    turn = bridge.turns._user_turn
    bridge.turns._mark_cancel(turn.task, "interrupted")
    await bridge.turns._offer_progress(turn, turn_progress.Progress("thinking"))
    await bridge.turns.cancel("interrupted")
    assert progress(await sent(bridge)) == [("preparing", None)]


# ── 2. Un toolCallId raro no rompe el turno ─────────────────────────────────


@pytest.mark.parametrize("call_id", [{"id": 1}, ["c1"], None, 3.5])
async def test_a_malformed_tool_call_id_never_fails_the_turn(make_bridge, interval, call_id):
    interval(0)
    bridge, _ = make_bridge(output_mode=WITH_PROGRESS)
    bridge.orchestrator.stream_response = _stream(
        _tool("start", "exec", call_id), _tool("result", "exec", call_id),
        OrchestratorEvent(type="token", content="hecho"),
    )
    await run_turn(bridge)
    msgs = await sent(bridge)
    assert msgs[-1] == {"type": "turn_end", "turn_id": "t-1", "reason": "done"}
    assert progress(msgs) == [("tool", "exec"), ("thinking", None)]
    assert not [m for m in msgs if m.get("type") == "error"]


async def test_a_tool_event_that_breaks_the_progress_only_loses_its_progress(
    make_bridge, interval, monkeypatch
):
    interval(0)

    def broken(self, tool_call):
        raise TypeError("deriva")

    monkeypatch.setattr(turn_progress.TurnProgress, "tool", broken)
    bridge, _ = make_bridge(output_mode=WITH_PROGRESS)
    bridge.orchestrator.stream_response = _stream(
        _tool("start"), OrchestratorEvent(type="token", content="hecho")
    )
    await run_turn(bridge)
    msgs = await sent(bridge)
    assert msgs[-1]["reason"] == "done"
    assert progress(msgs) == []


# ── 3. Sin texto, los deltas no tocan el progreso ───────────────────────────


@pytest.mark.parametrize(
    ("output_mode", "expected"),
    [
        # Sin `text` el cliente no ve el token: lo aplazado sigue siendo lo último que sabe.
        (("audio", "status"), [("preparing", None), ("tool", "read")]),
        # Con `text`, el token deja viejo lo aplazado y no sale.
        (("text", "status"), [("preparing", None)]),
    ],
)
async def test_deltas_reset_progress_only_for_clients_that_see_the_tokens(
    make_bridge, interval, output_mode, expected
):
    interval(0.1)
    bridge, _ = make_bridge(output_mode=output_mode, tts=_no_tts())
    bridge.orchestrator.stream_response = _stream(
        OrchestratorEvent(type="progress", phase="preparing"),
        _tool("start"),  # aplazado 0,1 s
        OrchestratorEvent(type="token", content="voy"),
        then_sleep=0.3,
    )
    await run_turn(bridge)
    assert progress(await sent(bridge)) == expected


# ── 5. Sin `status`, ni la cuenta de herramientas ───────────────────────────


async def test_no_tool_bookkeeping_for_clients_without_status(make_bridge, monkeypatch):
    calls = []
    real = turn_progress.TurnProgress.tool

    def spy(self, tool_call):
        calls.append(tool_call)
        return real(self, tool_call)

    monkeypatch.setattr(turn_progress.TurnProgress, "tool", spy)
    bridge, _ = make_bridge(output_mode=("text",))
    bridge.orchestrator.stream_response = _stream(_tool("start"), _tool("result"))
    await run_turn(bridge)
    assert calls == []
    assert progress(await sent(bridge)) == []


# ── 8. Fases de un push antes de su `agent start` ───────────────────────────


def _status(run_id, phase):
    return {"sessionKey": SK, "runId": run_id, "state": "status", "phase": phase}


async def _push_with_early_phases(bridge, run_id="r1"):
    turns_ = bridge.turns
    turns_.enqueue_push("chat", _status(run_id, "preparing_workspace"), run_id)
    turns_.enqueue_push("chat", _status(run_id, "starting_model"), run_id)
    turns_.enqueue_push("turn_start", SK, run_id)
    turns_.enqueue_push("chat", {"sessionKey": SK, "runId": run_id, "deltaText": "hola"}, run_id)
    turns_.enqueue_push("turn_end", SK, run_id)
    await bridge.push_idle()


async def test_the_last_early_phase_of_a_push_goes_out_when_it_opens(make_bridge, interval):
    interval(0)
    bridge, _ = make_bridge(client_id="hab_sito", output_mode=WITH_PROGRESS)
    await _push_with_early_phases(bridge)
    msgs = await sent(bridge)
    assert kinds(msgs) == ["turn_start", "turn_progress", "token", "turn_end"]
    assert progress(msgs) == [("thinking", None)]


async def test_early_phases_expire(make_bridge, interval, monkeypatch):
    interval(0)
    monkeypatch.setattr(turns, "_EARLY_PHASE_TTL_S", -1.0)
    bridge, _ = make_bridge(client_id="hab_sito", output_mode=WITH_PROGRESS)
    await _push_with_early_phases(bridge)
    assert progress(await sent(bridge)) == []


async def test_early_phases_are_bounded(make_bridge):
    bridge, _ = make_bridge(client_id="hab_sito", output_mode=WITH_PROGRESS)
    for i in range(turns._EARLY_PHASE_RUNS + 5):
        bridge.turns.enqueue_push("chat", _status(f"r{i}", "preparing_context"), f"r{i}")
    await bridge.push_idle()
    early = bridge.turns._early_phases
    assert len(early) == turns._EARLY_PHASE_RUNS
    assert "r0" not in early and f"r{turns._EARLY_PHASE_RUNS + 4}" in early


@pytest.mark.parametrize("output_mode", [("text",)])
async def test_early_phases_are_not_kept_for_clients_without_status(make_bridge, output_mode):
    bridge, _ = make_bridge(client_id="hab_sito", output_mode=output_mode)
    bridge.turns.enqueue_push("chat", _status("r1", "preparing_context"), "r1")
    await bridge.push_idle()
    assert not bridge.turns._early_phases


async def test_a_phase_of_another_run_does_not_land_on_the_open_push(make_bridge, interval):
    """Con un push abierto (r1), la fase de un run nuevo (r2) se guarda para r2."""
    interval(0)
    bridge, _ = make_bridge(client_id="hab_sito", output_mode=WITH_PROGRESS)
    bridge.turns.enqueue_push("turn_start", SK, "r1")
    bridge.turns.enqueue_push("chat", _status("r2", "preparing_context"), "r2")
    await bridge.push_idle()
    assert progress(await sent(bridge)) == []
    assert "r2" in bridge.turns._early_phases
