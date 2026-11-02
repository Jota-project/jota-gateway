"""El audio del TTS llega al cliente a ritmo de reproducción (7e, #224).

Solo se mira lo que recibe el cliente. 12 trozos de 100 ms = 1,2 s de audio; el adelanto
se reduce a 0,2 s para que cada test dure poco. Las tolerancias dejan margen para la CI.
"""

import time

import pytest

from src.core.config import settings
from tests.integration.harness.conftest import (
    assert_turns_paired,
    audio,
    of_type,
    turn_ended,
)

LEAD = 0.2
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
