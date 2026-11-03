"""El audio del TTS llega al cliente a ritmo de reproducción (7e, #224).

Solo se mira lo que recibe el cliente. 12 trozos de 100 ms = 1,2 s de audio; el adelanto
se reduce a 0,2 s para que cada test dure poco. Las tolerancias dejan margen para la CI.
"""

import asyncio
import json
import time

import pytest

from jota_gateway.core.config import settings
from tests.integration.harness.conftest import (
    assert_turns_paired,
    audio,
    of_type,
    turn_ended,
)

LEAD = 0.2
AUDIO_FRAME = b"\x00" * 3200  # 50 ms de micrófono: dispara el parcial del barge-in
CHUNKS = 12
BYTES_PER_S = 48000


@pytest.fixture
def paced(harness, monkeypatch):
    monkeypatch.setattr(settings, "AUDIO_LEAD_S", LEAD)
    harness.tts.chunk = b"\x01\x00" * 2400  # 100 ms
    harness.tts.chunks_per_reply = CHUNKS
    return harness


def stamp_frames(arrivals):
    """Predicado de `collect` que apunta cuándo llegó cada frame de audio."""

    def until(msgs):
        now = time.monotonic()
        while len(arrivals) < len(audio(msgs)):
            arrivals.append(now)
        return turn_ended(msgs) and len(arrivals) >= CHUNKS

    return until


async def test_audio_arrives_at_playback_pace(paced):
    client = await paced.connect(output_mode=("text", "audio"))
    await client.send({"type": "send", "text": "hola"})
    arrivals: list[float] = []
    msgs = await client.collect(stamp_frames(arrivals))

    sizes = [len(f) - 3 for f in audio(msgs)]
    played = 0
    for t, size in zip(arrivals, sizes, strict=True):
        played += size
        ahead = played / BYTES_PER_S - (t - arrivals[0])
        assert ahead <= LEAD + 0.1 + 0.15  # adelanto + un frame + margen de CI
    assert arrivals[-1] - arrivals[0] >= 0.75
    assert_turns_paired(msgs)


async def test_text_does_not_wait_behind_audio(paced):
    client = await paced.connect(output_mode=("text", "audio"))
    await client.send({"type": "send", "text": "hola"})
    msgs = await client.collect(lambda m: turn_ended(m) and len(audio(m)) >= CHUNKS)

    pos = {id(m): i for i, m in enumerate(msgs)}
    tokens = of_type(msgs, "token")
    frames = [m for m in msgs if isinstance(m, bytes)]
    assert pos[id(tokens[-1])] < pos[id(frames[-1])]
    assert pos[id(tokens[0])] < pos[id(frames[2])]


async def test_pacing_disabled_sends_audio_at_once(paced, monkeypatch):
    monkeypatch.setattr(settings, "AUDIO_PACING_ENABLED", False)
    client = await paced.connect(output_mode=("text", "audio"))
    await client.send({"type": "send", "text": "hola"})
    arrivals: list[float] = []
    msgs = await client.collect(stamp_frames(arrivals))

    assert len(audio(msgs)) == CHUNKS
    assert arrivals[-1] - arrivals[0] < 0.3


async def test_long_push_audio_is_not_cut(paced, monkeypatch):
    monkeypatch.setattr(settings, "PUSH_TTS_DRAIN_TIMEOUT_S", 0.4)
    client = await paced.connect(output_mode=("text", "audio"))
    await paced.push("aviso largo")
    msgs = await client.collect(lambda m: turn_ended(m) and len(audio(m)) >= CHUNKS)
    await client.collect(timeout=0.5)  # un segundo turn_end sobrante saltaría aquí

    assert len(audio(msgs)) == CHUNKS
    assert len(of_type(msgs, "turn_end")) == 1
    last_frame = max(i for i, m in enumerate(msgs) if isinstance(m, bytes))
    end = next(i for i, m in enumerate(msgs) if isinstance(m, dict) and m["type"] == "turn_end")
    assert end > last_frame
    assert_turns_paired(msgs)


async def test_push_waits_for_the_reply_being_spoken(paced):
    client = await paced.connect(output_mode=("text", "audio"))
    await client.send({"type": "send", "text": "hola"})
    await client.collect(lambda m: len(audio(m)) >= 1)
    await paced.push("aviso")
    msgs = await client.collect(lambda m: len(of_type(m, "turn_end")) >= 2)

    def index(kind):
        return [i for i, m in enumerate(msgs) if isinstance(m, dict) and m["type"] == kind]

    first_end, push_start = index("turn_end")[0], index("turn_start")[1]
    assert first_end < push_start
    push_seq = of_type(msgs, "turn_start")[1]["turn_seq"]
    push_frames = [i for i, m in enumerate(msgs) if isinstance(m, bytes) and int.from_bytes(m[1:3], "big") == push_seq]
    assert push_frames and min(push_frames) > first_end
    assert_turns_paired(msgs)


async def test_speaking_cuts_a_push_being_spoken(paced):
    client = await paced.connect(output_mode=("text", "audio"))
    await paced.push("aviso largo")
    await client.collect(lambda m: len(audio(m)) >= 1)
    await client.send({"type": "send", "text": "hola"})
    msgs = await client.collect(lambda m: len(of_type(m, "turn_end")) >= 2)
    await client.collect(timeout=0.3)  # un turn_end sobrante del push saltaría aquí

    push, normal = of_type(msgs, "turn_start")
    push_end = [m for m in of_type(msgs, "turn_end") if m["turn_id"] == push["turn_id"]]
    assert len(push_end) == 1
    pos = {id(m): i for i, m in enumerate(msgs)}
    assert pos[id(push_end[0])] < pos[id(normal)]
    late = [
        m for m in msgs[pos[id(push_end[0])]:]
        if isinstance(m, bytes) and int.from_bytes(m[1:3], "big") == push["turn_seq"]
    ]
    assert not late
    assert "".join(t["text"] for t in of_type(msgs, "token") if t["turn_id"] == normal["turn_id"]) == "reply-hola"
    assert not of_type(msgs, "error")
    assert_turns_paired(msgs)


def _tokens(msgs, turn_id):
    return "".join(t["text"] for t in of_type(msgs, "token") if t["turn_id"] == turn_id)


def _seq_audio(msgs, seq):
    return [m for m in msgs if isinstance(m, bytes) and int.from_bytes(m[1:3], "big") == seq]


async def test_a_queued_push_survives_a_new_send(paced):
    """I-1: un push en cola tras la respuesta que suena espera también a la siguiente."""
    client = await paced.connect(output_mode=("text", "audio"))
    await client.send({"type": "send", "text": "hola"})
    await client.collect(lambda m: len(audio(m)) >= 1)
    await paced.push("aviso")
    await asyncio.sleep(0.2)  # el push espera detrás de la respuesta que suena
    await client.send({"type": "send", "text": "otra"})
    msgs = await client.collect(lambda m: len(of_type(m, "turn_end")) >= 3)
    await client.collect(timeout=0.3)

    starts = of_type(msgs, "turn_start")
    assert len(starts) == 3
    first, second, push = starts
    assert _tokens(msgs, second["turn_id"]) == "reply-otra"
    assert _tokens(msgs, push["turn_id"]) == "aviso"
    pos = {id(m): i for i, m in enumerate(msgs)}
    second_end = next(m for m in of_type(msgs, "turn_end") if m["turn_id"] == second["turn_id"])
    assert pos[id(second_end)] < pos[id(push)]
    assert_turns_paired(msgs)


async def test_slow_reader_with_keepalive_gets_the_whole_reply(paced):
    """Criterio 1: un cliente que reproduce a 1× con keepalive corto no se desconecta."""
    client = await paced.connect(
        output_mode=("text", "audio"), ping_interval=0.3, ping_timeout=0.3
    )
    await client.send({"type": "send", "text": "hola"})
    received: list = []
    async with asyncio.timeout(5):
        while not turn_ended(received):
            raw = await client.ws.recv()
            if isinstance(raw, bytes):
                received.append(raw)
                await asyncio.sleep((len(raw) - 3) / BYTES_PER_S)  # lo «reproduce»
            else:
                received.append(json.loads(raw))
    assert len(b"".join(f[3:] for f in audio(received))) == CHUNKS * 4800
    assert_turns_paired(received)


async def _audio_client(paced):
    paced.transcriber.on_audio = [("espera un momento", False)]
    return await paced.connect(input_mode="audio", output_mode=("text", "audio"))


async def test_barge_in_during_paced_audio_stops_it_soon(paced):
    """Criterio 5: tras el barge-in llega como mucho el adelanto ya en camino."""
    client = await _audio_client(paced)
    await client.send({"type": "send", "text": "hola"})
    await client.collect(lambda m: len(audio(m)) >= 1)
    before = len(client.messages)
    await client.send_audio(AUDIO_FRAME)
    msgs = await client.collect(lambda m: bool(of_type(m, "interrupted")))
    await client.collect(timeout=0.3)

    (turn,) = of_type(msgs, "turn_start")
    late = _seq_audio(msgs[before:], turn["turn_seq"])
    assert sum(len(f) - 3 for f in late) <= (LEAD + 0.1 + 0.3) * BYTES_PER_S
    ends = of_type(msgs, "turn_end")
    (interrupted,) = of_type(msgs, "interrupted")
    assert ends == [{"type": "turn_end", "turn_id": turn["turn_id"]}]
    assert interrupted["turn_id"] == turn["turn_id"]
    assert msgs.index(ends[0]) < msgs.index(interrupted)


async def test_barge_in_cuts_a_push_being_spoken(paced):
    """Criterio 9: un barge-in corta el push que suena: un turn_end, interrupted con su id."""
    client = await _audio_client(paced)
    await paced.push("aviso largo")
    await client.collect(lambda m: len(audio(m)) >= 1)
    await client.send_audio(AUDIO_FRAME)
    msgs = await client.collect(lambda m: bool(of_type(m, "interrupted")))
    await client.collect(timeout=0.3)

    (push,) = of_type(msgs, "turn_start")
    ends = of_type(msgs, "turn_end")
    (interrupted,) = of_type(msgs, "interrupted")
    assert ends == [{"type": "turn_end", "turn_id": push["turn_id"]}]
    assert interrupted["turn_id"] == push["turn_id"]
    end_at = msgs.index(ends[0])
    assert end_at < msgs.index(interrupted)
    assert not _seq_audio(msgs[end_at:], push["turn_seq"])
    assert_turns_paired(msgs)
