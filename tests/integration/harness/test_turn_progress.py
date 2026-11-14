"""`turn_progress` visto desde el cliente (#254): el gateway real contra el OpenClaw falso.

OpenClaw cuenta en qué está (`chat` `state:"status"`, `session.tool`) mientras el cliente no
recibe nada; el gateway lo reenvía con su propio vocabulario (`preparing`, `thinking`, `tool`)
a quien tiene `status` en `output_mode`, siempre dentro de su turno.
"""

from itertools import pairwise

import pytest
from sqlmodel import Session

from fogar_gateway.db.models import ClientRecord
from fogar_gateway.services import turn_progress
from tests.integration.conftest import CLIENT_ID
from tests.integration.harness.conftest import (
    assert_turns_paired,
    interrupted,
    of_type,
    turn_ended,
)

WITH_PROGRESS = ("text", "status")
AUDIO_FRAME = b"\x00" * 3200  # 50 ms de PCM Float32 a 16 kHz


@pytest.fixture
def no_rate_cap(monkeypatch):
    """Sin el tope de ritmo: el falso manda sus frames seguidos y aquí se mira el contenido."""
    monkeypatch.setattr(turn_progress, "PROGRESS_MIN_INTERVAL_S", 0.0)


def progress(messages, turn_id=None):
    return [
        (m["phase"], m["tool"])
        for m in of_type(messages, "turn_progress")
        if turn_id is None or m["turn_id"] == turn_id
    ]


def assert_progress_inside_turns(messages):
    """Cada `turn_progress` llega con su turno abierto: después de su `turn_start` y antes de
    su `turn_end`, y nunca antes que el primer token del turno siguiente."""
    open_turn = None
    for m in messages:
        if not isinstance(m, dict):
            continue
        kind = m.get("type")
        if kind == "turn_start":
            open_turn = m["turn_id"]
        elif kind == "turn_end":
            if m["turn_id"] == open_turn:
                open_turn = None
        elif kind == "turn_progress":
            assert m["turn_id"] == open_turn, f"turn_progress fuera de su turno: {m}"


def fin_arrived(messages):
    return any(t["text"] == "fin" for t in of_type(messages, "token")) and turn_ended(messages)


async def _turn(harness, text, output_mode=WITH_PROGRESS):
    client = await harness.connect(output_mode=output_mode)
    await client.send({"type": "send", "text": text})
    msgs = await client.collect(turn_ended)
    return client, msgs


async def test_progress_arrives_inside_the_turn_in_order(harness, no_rate_cap):
    _, msgs = await _turn(harness, "progress")
    (start,) = of_type(msgs, "turn_start")
    # Fases de arranque → preparing (una vez: sin repetir), starting_model → thinking; la
    # herramienta con su nombre y, al terminar, thinking otra vez.
    assert progress(msgs, start["turn_id"]) == [
        ("preparing", None),
        ("thinking", None),
        ("tool", "ha_call_service"),
        ("thinking", None),
    ]
    kinds = [m["type"] for m in msgs if isinstance(m, dict) and m["type"] != "pipeline_event"]
    assert kinds.index("turn_start") < kinds.index("turn_progress")
    last_progress = len(kinds) - 1 - kinds[::-1].index("turn_progress")
    assert last_progress < kinds.index("token") < kinds.index("turn_end")
    assert_progress_inside_turns(msgs)
    assert_turns_paired(msgs)


async def test_tool_progress_carries_the_name_only(harness, db_engine, no_rate_cap):
    # Con tool_calls_enabled el `tool_call` completo sigue llegando como siempre; el progreso
    # no lleva más que el nombre, con o sin él.
    with Session(db_engine) as s:
        record = s.get(ClientRecord, CLIENT_ID)
        assert record is not None
        record.tool_calls_enabled = True
        s.add(record)
        s.commit()
    client, msgs = await _turn(harness, "progress")
    for m in of_type(msgs, "turn_progress"):
        assert set(m) == {"type", "turn_id", "phase", "tool"}
    assert len(of_type(msgs, "tool_call")) == 2
    progress_raw = [raw for raw in client.raw_texts if '"turn_progress"' in raw]
    assert progress_raw and not [raw for raw in progress_raw if "SECRET" in raw]


async def test_without_tool_calls_enabled_nothing_of_the_tool_but_its_name(harness, no_rate_cap):
    client, msgs = await _turn(harness, "progress")
    assert ("tool", "ha_call_service") in progress(msgs)
    assert not of_type(msgs, "tool_call")
    assert not [raw for raw in client.raw_texts if "SECRET" in raw]


async def test_unknown_phase_and_unsafe_tool_name_are_not_forwarded(harness, no_rate_cap):
    client, msgs = await _turn(harness, "progress_weird")
    assert progress(msgs) == [("preparing", None), ("tool", None), ("thinking", None)]
    assert not [raw for raw in client.raw_texts if "warp_drive" in raw or "rm -rf" in raw]


@pytest.mark.parametrize("output_mode", [("text",), ("audio", "text")])
async def test_only_clients_with_status_get_progress(harness, no_rate_cap, output_mode):
    _, msgs = await _turn(harness, "progress", output_mode=output_mode)
    assert of_type(msgs, "token")
    assert not of_type(msgs, "turn_progress")


async def test_nothing_after_turn_end(harness, no_rate_cap):
    """Una fase y una herramienta del run después de su `final` no llegan (ni como push)."""
    client, msgs = await _turn(harness, "progress_late")
    await harness.push("fin")
    msgs = await client.collect(fin_arrived)
    assert progress(msgs) == []
    assert_progress_inside_turns(msgs)
    assert_turns_paired(msgs)


async def test_nothing_from_an_aborted_run(harness, no_rate_cap):
    client = await harness.connect(output_mode=WITH_PROGRESS)
    await client.send({"type": "send", "text": "waitprogress"})
    await harness.openclaw.wait_held(1)
    await client.send({"type": "cancel"})
    await client.collect(turn_ended)
    await harness.openclaw.finish_next()  # el run cancelado manda su fase y su herramienta
    await harness.push("fin")
    msgs = await client.collect(fin_arrived)
    assert progress(msgs) == []
    assert of_type(msgs, "turn_end")[0]["reason"] == "cancelled"
    assert_turns_paired(msgs)


async def test_push_turns_get_progress_too(harness, no_rate_cap):
    client = await harness.connect(output_mode=WITH_PROGRESS)
    await harness.push("aviso", progress=True)
    msgs = await client.collect(turn_ended)
    (start,) = of_type(msgs, "turn_start")
    assert start["origin"] == "push"
    assert progress(msgs, start["turn_id"]) == [
        ("preparing", None),
        ("thinking", None),
        ("tool", "push_tool"),
        ("thinking", None),
    ]
    assert_progress_inside_turns(msgs)
    assert_turns_paired(msgs)


async def test_a_deferred_progress_never_goes_out_after_turn_end(harness):
    """Con el tope por defecto, lo aplazado de un turno que termina antes no sale nunca."""
    client, msgs = await _turn(harness, "progress")
    await harness.push("fin")
    msgs = await client.collect(fin_arrived)
    seen = progress(msgs)
    assert seen and seen[0] == ("preparing", None)
    assert_progress_inside_turns(msgs)
    assert_turns_paired(msgs)


async def test_a_flood_is_rate_capped_and_the_latest_state_still_arrives(harness):
    """Con el tope por defecto: 20 herramientas seguidas no son 40 mensajes, y lo último que
    ve el cliente antes del texto es el estado real (`thinking`), no una herramienta vieja."""
    harness.openclaw.burst_tools = 20
    harness.openclaw.burst_pause_s = 0.6
    _, msgs = await _turn(harness, "progress_burst")
    seen = progress(msgs)
    assert 1 <= len(seen) <= 4, seen
    assert seen[-1] == ("thinking", None)
    assert all(a != b for a, b in pairwise(seen)), "repetido seguido"
    assert_progress_inside_turns(msgs)
    assert_turns_paired(msgs)


async def test_no_progress_after_a_barge_in_with_a_tool_deferred(harness):
    """La fase sale ya y la herramienta queda aplazada por el ritmo; el usuario habla encima
    antes de que salga: no llega ningún `turn_progress` tras la interrupción."""
    harness.transcriber.on_audio = [("espera un momento", False)]
    client = await harness.connect(input_mode="audio", output_mode=WITH_PROGRESS)
    await client.send({"type": "send", "text": "progress_hold"})
    await client.collect(lambda m: bool(of_type(m, "turn_progress")))
    await client.send_audio(AUDIO_FRAME)
    await client.collect(lambda m: bool(interrupted(m)))
    msgs = await client.collect(timeout=turn_progress.PROGRESS_MIN_INTERVAL_S + 0.4)
    end_at = msgs.index(interrupted(msgs)[0])
    assert not of_type(msgs[end_at:], "turn_progress")
    assert ("tool", "slow_tool") not in progress(msgs)
    assert_progress_inside_turns(msgs)
    assert_turns_paired(msgs)


async def test_a_push_shows_the_phase_it_reported_before_opening(harness, no_rate_cap):
    """OpenClaw cuenta las fases de arranque antes del `agent start` del push (#300): la
    última sale como primer progreso del push."""
    client = await harness.connect(output_mode=WITH_PROGRESS)
    await harness.push("aviso", progress="early")
    msgs = await client.collect(turn_ended)
    kinds = [m["type"] for m in msgs if isinstance(m, dict) and m["type"] != "pipeline_event"]
    start_at = kinds.index("turn_start")
    assert kinds[start_at + 1] == "turn_progress"
    assert progress(msgs) == [("preparing", None)]
    assert_turns_paired(msgs)


async def test_agent_events_of_other_streams_neither_open_nor_close_a_push(harness):
    """`agent` `item`/`tool` con `data.phase` start/end (2026.10.5): ni abren un push ni
    cierran el abierto; solo el stream `lifecycle` lo hace."""
    client = await harness.connect()
    await harness.openclaw.agent_noise(harness.session_key())  # un run sin lifecycle
    await harness.push("aviso", end=False)
    await client.collect(lambda m: bool(of_type(m, "token")))
    await harness.openclaw.agent_noise(harness.session_key())  # item end del push abierto
    await harness.push_end()
    msgs = await client.collect(turn_ended)
    assert len(of_type(msgs, "turn_start")) == 1
    assert [m["type"] for m in msgs if isinstance(m, dict)][-1] == "turn_end"
    assert of_type(msgs, "turn_end")[0]["reason"] == "done"
    assert_turns_paired(msgs)
