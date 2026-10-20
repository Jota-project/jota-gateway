import math
import struct

from src.services.speech.audio import EnergyGate, f32le_to_s16le

RATE = 16000
FRAME = RATE // 50  # 20 ms


def _f32(values) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def _s16(data: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(data) // 2}h", data))


def _silence(n=FRAME) -> bytes:
    return _f32([0.0] * n)


def _speech(n=FRAME) -> bytes:
    return _f32([0.3 * math.sin(i / 5) for i in range(n)])


def test_f32_to_s16_saturates():
    assert _s16(f32le_to_s16le(_f32([1.5, -1.5]))) == [32767, -32768]


def test_f32_to_s16_nan_is_zero():
    assert _s16(f32le_to_s16le(_f32([float("nan"), 0.0]))) == [0, 0]


def test_f32_to_s16_drops_partial_sample():
    assert len(f32le_to_s16le(_f32([0.5]) + b"\x01\x02")) == 2


def test_gate_silence_forwards_nothing():
    gate = EnergyGate()
    for _ in range(100):
        out, ended = gate.feed(_silence())
        assert out == [] and ended is False


def test_gate_preroll_included():
    gate = EnergyGate(preroll_s=0.3)
    for _ in range(25):  # 0.5 s of silence
        gate.feed(_silence())
    out, ended = gate.feed(_speech())
    assert ended is False
    total = sum(len(c) for c in out) / 4 / RATE
    assert 0.29 <= total <= 0.34  # ~0.3 s preroll + the 20 ms speech frame


def test_gate_ends_segment_after_hangover():
    gate = EnergyGate(hangover_s=1.0)
    gate.feed(_speech())
    ended_at = None
    for i in range(80):
        out, ended = gate.feed(_silence())
        assert len(out) == 1  # still forwarded while in segment
        if ended:
            ended_at = i
            break
    assert ended_at is not None
    assert abs((ended_at + 1) * 0.02 - 1.0) < 0.05
    out, ended = gate.feed(_silence())
    assert out == [] and ended is False


def test_gate_short_pause_inside_hangover_keeps_segment():
    gate = EnergyGate(hangover_s=1.0)
    gate.feed(_speech())
    for _ in range(25):  # 0.5 s pause
        _, ended = gate.feed(_silence())
        assert ended is False
    out, ended = gate.feed(_speech())
    assert out and ended is False
    for _ in range(25):
        _, ended = gate.feed(_silence())
        assert ended is False


def test_energy_gate_ignores_frames_shorter_than_one_sample():
    gate = EnergyGate()
    for n in (0, 1, 3):
        assert gate.feed(b"\x00" * n) == ([], False)
    assert len(gate._ring) == 0


def test_s16le_24k_to_f32le_16k_length_and_values():
    from src.services.speech.audio import s16le_24k_to_f32le_16k

    n = 2400  # 0,1 s a 24 kHz
    pcm = struct.pack(f"<{n}h", *([16384] * n))  # constante 0,5
    out = s16le_24k_to_f32le_16k(pcm)
    vals = struct.unpack(f"<{len(out) // 4}f", out)
    assert len(vals) == 1600  # 0,1 s a 16 kHz
    assert all(abs(v - 0.5) < 1e-3 for v in vals)
    assert s16le_24k_to_f32le_16k(b"") == b""
    assert s16le_24k_to_f32le_16k(b"\x01") == b""  # byte suelto: sin muestras
    ramp = struct.pack("<4h", 0, 3000, 6000, 9000)
    r = struct.unpack("<2f", s16le_24k_to_f32le_16k(ramp))
    assert r[0] == 0.0 and abs(r[1] - 4500 / 32768) < 1e-6  # posición 1,5 interpolada
