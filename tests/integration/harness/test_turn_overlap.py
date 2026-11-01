"""Turnos que se pisan, vistos desde el cliente (arnés e2e, M7a).

Bugs conocidos de la auditoría, todos para M10 (TurnController): el gateway enruta los frames
de OpenClaw por `sessionKey` y no por `runId`, y no garantiza un solo turno abierto por sesión.
Cada test asierta el comportamiento correcto según docs/client-protocol.md (xfail estricto:
empezará a pasar cuando se arregle).

Límite de realismo de los dos tests tras `chat.abort`: con OpenClaw 2026.10.5 solo se observó
`chat aborted` + `agent end` tras el abort. El falso reproduce frames que ya iban en camino
cuando llegó el abort (una carrera), no frames emitidos mucho después.

Barrera de orden: el socket de OpenClaw se procesa en orden y los push pasan por un único
worker FIFO, así que un push "fin" posterior llega al cliente detrás de cualquier turno fantasma.
"""

import pytest

from src.core.config import settings
from tests.integration.harness.conftest import assert_turns_paired, of_type, turn_ended

AUDIO_FRAME = b"\x00" * 3200  # 50 ms de PCM Float32 a 16 kHz
M10 = "se arregla en M10 (TurnController)"


def tokens(messages, turn_id):
    return "".join(m["text"] for m in of_type(messages, "token") if m["turn_id"] == turn_id)


def fin_arrived(messages):
    """Llegó el push de barrera "fin" y todos los turnos están cerrados."""
    return any(t["text"] == "fin" for t in of_type(messages, "token")) and turn_ended(messages)


def assert_one_turn_at_a_time(messages):
    """Ningún `turn_start` llega mientras otro turno sigue abierto."""
    open_turn = None
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("type") == "turn_start":
            assert open_turn is None, f"{m['turn_id']} empieza con {open_turn} abierto"
            open_turn = m["turn_id"]
        elif m.get("type") == "turn_end" and m["turn_id"] == open_turn:
            open_turn = None


async def _barge_in(harness, text):
    """Turno `text` que el orquestador deja en cola y que el usuario interrumpe hablando."""
    harness.transcriber.on_audio = [("espera un momento", False)]
    client = await harness.connect(input_mode="audio", output_mode=("text",))
    await client.send({"type": "send", "text": text})
    await harness.openclaw.wait_held(1)
    await client.send_audio(AUDIO_FRAME)
    await client.collect(lambda m: bool(of_type(m, "interrupted")))
    return client


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="G-14: la respuesta tardía (ya en camino al abortar) del turno interrumpido cierra "
    f"el turno nuevo; {M10}",
)
async def test_late_reply_of_an_interrupted_turn_does_not_leak_into_the_next(harness):
    client = await _barge_in(harness, "wait")
    await client.send({"type": "send", "text": "wait again"})
    await harness.openclaw.wait_held(2)  # el turno nuevo ya espera su respuesta
    await harness.openclaw.finish_next()  # llega tarde la respuesta del interrumpido
    await harness.openclaw.finish_next()  # y después la del turno nuevo
    await client.collect(lambda m: len(of_type(m, "turn_end")) >= 2)  # t-2 cerrado
    await harness.push("fin")
    msgs = await client.collect(fin_arrived)

    first, second = of_type(msgs, "turn_start")[:2]
    assert tokens(msgs, first["turn_id"]) == ""
    assert tokens(msgs, second["turn_id"]) == "reply-wait again", "G-14"
    assert len(of_type(msgs, "turn_start")) == 3  # t-1, t-2 y el push "fin"
    assert_turns_paired(msgs)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="G-14/G-15: el turno interrumpido (frames ya en camino al abortar) reaparece como "
    f"un push fantasma; {M10}",
)
async def test_an_interrupted_turn_does_not_come_back_as_a_push(harness):
    client = await _barge_in(harness, "wait")
    await harness.openclaw.finish_next()  # el run interrumpido termina igualmente
    await harness.push("fin")
    msgs = await client.collect(fin_arrived)

    starts = of_type(msgs, "turn_start")
    assert len(starts) == 2, f"G-15: turno fantasma tras interrupted: {starts[1:-1]}"
    assert not [t for t in of_type(msgs, "token") if t["text"] == "reply-wait"]
    assert_turns_paired(msgs)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=f"G-15: un push sin `end` nunca recibe su turn_end; {M10}",
)
async def test_a_silent_push_is_closed_like_any_other_turn(harness, monkeypatch):
    monkeypatch.setattr(settings, "TURN_TIMEOUT_S", 0.3)
    client = await harness.connect()
    await harness.push("primer aviso", end=False)  # su `end` no llega nunca
    await client.collect(lambda m: bool(of_type(m, "token")))
    msgs = await client.collect(timeout=1.0)
    (first,) = of_type(msgs, "turn_start")
    assert of_type(msgs, "turn_end"), f"G-15: {first['turn_id']} sigue abierto sin progreso"

    await harness.push("segundo aviso")
    msgs = await client.collect(lambda m: len(of_type(m, "turn_start")) == 2)
    second = of_type(msgs, "turn_start")[1]
    assert tokens(msgs, second["turn_id"]) == "segundo aviso"
    assert_turns_paired(await client.collect(turn_ended))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="#202: un push abierto y un turno normal se solapan en el mismo socket; "
    f"{M10} — https://github.com/Jota-project/jota-gateway/issues/202",
)
async def test_a_push_and_a_normal_turn_never_overlap(harness):
    client = await harness.connect()
    await harness.push("tienes una reunión", end=False)
    await client.collect(lambda m: bool(of_type(m, "token")))
    await client.send({"type": "send", "text": "hola"})
    try:  # barrera: hoy el turno normal arranca; si M10 lo difiere, no llega nada y se sigue
        await client.collect(lambda m: len(of_type(m, "turn_start")) == 2, timeout=5.0)
    except AssertionError:
        pass
    await harness.push_end()
    msgs = await client.collect(lambda m: len(of_type(m, "turn_end")) == 2)

    assert_one_turn_at_a_time(msgs)
    normal = of_type(msgs, "turn_start")[1]
    assert tokens(msgs, normal["turn_id"]) == "reply-hola"
    assert_turns_paired(msgs)
