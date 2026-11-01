"""Los flujos principales del gateway vistos desde el cliente (arnés e2e, M7a).

Cada test dice qué hace el orquestador (responde, se queda esperando, falla, se cae,
inicia un turno) y comprueba solo lo que recibe el cliente por su WebSocket.
"""

from tests.integration.harness.conftest import (
    assert_turns_paired,
    audio,
    of_type,
    turn_ended,
)

AUDIO_FRAME = b"\x00" * 3200  # 50 ms de PCM Float32 a 16 kHz


def tokens(messages, turn_id):
    return "".join(m["text"] for m in of_type(messages, "token") if m["turn_id"] == turn_id)


def turn_errors(messages):
    return [(e["code"], e["message"], e["fatal"]) for e in of_type(messages, "error")]


async def test_text_turn_streams_the_reply(harness):
    client = await harness.connect()
    await client.send({"type": "send", "text": "hola"})
    msgs = await client.collect(turn_ended)

    (start,) = of_type(msgs, "turn_start")
    assert tokens(msgs, start["turn_id"]) == "reply-hola"
    assert of_type(msgs, "error") == []
    assert_turns_paired(msgs)


async def test_voice_turn_transcribes_and_speaks_the_reply(harness):
    harness.transcriber.on_end = [("enciende la luz", True)]
    client = await harness.connect(input_mode="audio", output_mode=("audio", "text"))
    assert client.ready["live_capabilities"]["transcriber"] is True
    assert client.ready["live_capabilities"]["tts"] is True

    await client.send_audio(AUDIO_FRAME)
    await client.send({"type": "end"})
    msgs = await client.collect(lambda m: bool(of_type(m, "transcription")))
    (transcription,) = of_type(msgs, "transcription")
    assert transcription["text"] == "enciende la luz"
    assert of_type(msgs, "turn_start") == []  # el cliente decide cuándo enviarla

    pcm = harness.tts.chunk * harness.tts.chunks_per_reply
    await client.send({"type": "send", "text": transcription["text"]})
    msgs = await client.collect(
        lambda m: turn_ended(m) and sum(len(f) - 3 for f in audio(m)) >= len(pcm)
    )

    (start,) = of_type(msgs, "turn_start")
    assert tokens(msgs, start["turn_id"]) == "reply-enciende la luz"
    frames = audio(msgs)
    assert all(f[0] == 0xA1 and int.from_bytes(f[1:3], "big") == start["turn_seq"] for f in frames)
    assert b"".join(f[3:] for f in frames) == pcm
    assert_turns_paired(msgs)


async def test_speaking_over_a_running_turn_interrupts_it(harness):
    harness.transcriber.on_audio = [("espera un momento", False)]
    client = await harness.connect(input_mode="audio", output_mode=("text",))
    assert client.ready["live_capabilities"]["barge_in"] is True

    await client.send({"type": "send", "text": "hold"})  # el orquestador se queda esperando
    await client.collect(lambda m: bool(of_type(m, "turn_start")))
    await client.send_audio(AUDIO_FRAME)
    msgs = await client.collect(lambda m: bool(of_type(m, "interrupted")))

    (start,) = of_type(msgs, "turn_start")
    assert of_type(msgs, "transcription_partial")[0]["text"] == "espera un momento"
    types = [m.get("type") for m in msgs if isinstance(m, dict)]
    (end,) = of_type(msgs, "turn_end")
    (interrupted,) = of_type(msgs, "interrupted")
    assert end["turn_id"] == interrupted["turn_id"] == start["turn_id"]
    assert types.index("turn_end") < types.index("interrupted")
    assert_turns_paired(msgs)


async def test_orchestrator_push_reaches_the_client_as_one_turn(harness):
    client = await harness.connect()
    await harness.push("tienes una reunión en 5 minutos")
    await client.collect(turn_ended)
    msgs = await client.collect(timeout=0.3)  # nada más detrás: ningún turno duplicado

    (start,) = of_type(msgs, "turn_start")
    assert len(of_type(msgs, "turn_end")) == 1
    assert tokens(msgs, start["turn_id"]) == "tienes una reunión en 5 minutos"
    assert_turns_paired(msgs)


async def test_orchestrator_dropping_mid_turn_ends_the_turn_with_a_stable_error(harness):
    client = await harness.connect()
    await client.send({"type": "send", "text": "drop"})  # responde a medias y se cae

    def orchestrator_down(m):
        return [
            s
            for s in of_type(m, "status")
            if s["service"] == "orchestrator" and s["state"] in ("unavailable", "reconnecting")
        ]

    msgs = await client.collect(lambda m: turn_ended(m) and bool(orchestrator_down(m)))

    (start,) = of_type(msgs, "turn_start")
    assert turn_errors(msgs) == [("TURN_ERROR", "orchestrator_disconnected", False)]
    assert of_type(msgs, "error")[0]["turn_id"] == start["turn_id"]
    assert_turns_paired(msgs)


async def test_orchestrator_failure_ends_the_turn_and_the_session_goes_on(harness):
    client = await harness.connect()
    await client.send({"type": "send", "text": "fail"})  # el orquestador rechaza el turno
    msgs = await client.collect(turn_ended)

    (start,) = of_type(msgs, "turn_start")
    assert turn_errors(msgs) == [("TURN_ERROR", "orchestrator_error", False)]
    assert of_type(msgs, "error")[0]["turn_id"] == start["turn_id"]
    leaked = [t for t in client.raw_texts if "provider exploded" in t or "UNAVAILABLE" in t]
    assert leaked == []

    await client.send({"type": "send", "text": "otra vez"})
    msgs = await client.collect(lambda m: len(of_type(m, "turn_end")) == 2)
    second = of_type(msgs, "turn_start")[1]
    assert tokens(msgs, second["turn_id"]) == "reply-otra vez"
    assert_turns_paired(msgs)


async def test_agent_failing_after_accepting_ends_the_turn_at_once(harness):
    # #300: OpenClaw acepta el turno y luego el agente falla (chat state:error).
    client = await harness.connect()
    await client.send({"type": "send", "text": "agent_error"})
    msgs = await client.collect(turn_ended, timeout=3)

    (start,) = of_type(msgs, "turn_start")
    assert turn_errors(msgs) == [("TURN_ERROR", "orchestrator_error", False)]
    assert of_type(msgs, "error")[0]["turn_id"] == start["turn_id"]
    assert [t for t in client.raw_texts if "No API key" in t] == []

    await client.send({"type": "send", "text": "otra vez"})
    msgs = await client.collect(lambda m: len(of_type(m, "turn_end")) == 2)
    second = of_type(msgs, "turn_start")[1]
    assert tokens(msgs, second["turn_id"]) == "reply-otra vez"
    assert_turns_paired(msgs)


async def test_failing_push_still_ends_its_turn(harness):
    # #300: un push cuyo agente falla acaba en agent phase:error, no en end.
    client = await harness.connect()
    await harness.push("recordatorio", end=False)
    await client.collect(lambda m: bool(of_type(m, "turn_start")))
    await harness.push_fail()
    msgs = await client.collect(turn_ended, timeout=3)

    assert len(of_type(msgs, "turn_end")) == 1
    assert_turns_paired(msgs)
