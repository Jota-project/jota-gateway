"""Utilidades de audio para proveedores de voz: conversión de formato y puerta de energía."""

from __future__ import annotations

import math
import sys
from array import array
from collections import deque

_BIG = sys.byteorder == "big"


def _floats(frame: bytes) -> array:
    arr = array("f")
    arr.frombytes(frame[: len(frame) - (len(frame) % 4)])
    if _BIG:
        arr.byteswap()
    return arr


def f32le_to_s16le(frame: bytes) -> bytes:
    """Float32 LE -> PCM16 LE. Satura a [-1, 1], NaN -> 0, descarta bytes sobrantes."""
    out = array("h")
    for x in _floats(frame):
        if x != x:
            out.append(0)
        else:
            x = max(-1.0, min(1.0, x))
            out.append(32767 if x >= 1.0 else int(x * 32768.0) if x < 0 else int(x * 32767.0))
    if _BIG:
        out.byteswap()
    return out.tobytes()


def _rms_dbfs(frame: bytes) -> float:
    samples = _floats(frame)
    if not samples:
        return -math.inf
    power = sum(x * x for x in samples if x == x) / len(samples)
    return 10.0 * math.log10(power) if power > 0 else -math.inf


class EnergyGate:
    """Deja pasar solo el audio con voz (más preroll) para no pagar por silencio.

    `feed()` devuelve (chunks Float32 a reenviar, segmento_terminado). Tras `hangover_s`
    seguidos bajo el umbral se cierra el segmento: el llamante fuerza el fin de frase.
    """

    def __init__(
        self,
        rms_dbfs: float = -45.0,
        preroll_s: float = 0.3,
        hangover_s: float = 1.5,
        sample_rate: int = 16000,
    ) -> None:
        self._threshold = rms_dbfs
        self._preroll_s = preroll_s
        self._hangover_s = hangover_s
        self._rate = sample_rate
        self.reset()

    def reset(self) -> None:
        self._ring: deque[tuple[bytes, float]] = deque()
        self._ring_s = 0.0
        self._in_segment = False
        self._quiet_s = 0.0

    def feed(self, frame_f32: bytes) -> tuple[list[bytes], bool]:
        dur = (len(frame_f32) // 4) / self._rate
        loud = _rms_dbfs(frame_f32) >= self._threshold
        if not self._in_segment:
            if not loud:
                self._ring.append((frame_f32, dur))
                self._ring_s += dur
                while self._ring and self._ring_s - self._ring[0][1] >= self._preroll_s - 1e-9:
                    self._ring_s -= self._ring.popleft()[1]
                return [], False
            out = [c for c, _ in self._ring] + [frame_f32]
            self._ring.clear()
            self._ring_s = 0.0
            self._in_segment = True
            self._quiet_s = 0.0
            return out, False
        if loud:
            self._quiet_s = 0.0
            return [frame_f32], False
        self._quiet_s += dur
        if self._quiet_s >= self._hangover_s - 1e-9:
            self._in_segment = False
            self._quiet_s = 0.0
            return [frame_f32], True
        return [frame_f32], False
