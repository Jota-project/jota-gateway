"""
linea_viva.py
~~~~~~~~~~~~~
La marca animada de Fogar en la terminal: la línea viva (fogar-brand, `linea-viva/`) pintada con
caracteres Braille (2×4 puntos por celda). La entrada sigue las reglas de la marca: la línea
aparece entera con una ola, se coloca bajo sus letras y se escribe de izquierda a derecha; nunca se
dibuja avanzando. La brasa (rescoldo → ascua → llama → chispa) es solo luz: el filamento que la
recorre y el frente que sube. Al acabar queda la palabra quieta, en tinta (sin color).

`rich` se importa al pintar; los datos (`linea_viva_datos.py`), al usarse.
"""

import math
import os
import sys
import time
from typing import Any

COLS = 56  # ancho de la palabra en celdas; por debajo de MIN_COLS de terminal no se pinta
MIN_COLS = 60
DURATION_S = 2.7
FPS = 30
# Paleta de fogar-brand (marca/paleta.css): la brasa, en cuatro pasos.
EMBER = ["#5a1a10", "#b8361a", "#ee6a2c", "#ffe1b8"]
_STOPS = (0.0, 0.35, 0.7, 1.0)
_DOT = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
_BRUSH = ((0, 0), (1, 0), (0, 1), (1, 1))  # 2 por 2: con un solo punto la línea se ve punteada


def _smooth(b: float) -> float:
    return b * b * b * (b * (b * 6 - 15) + 10)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def ember(h: float) -> str:
    """Calor 0..1 → color de la brasa, interpolado entre sus cuatro pasos."""
    rgb = [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in EMBER]
    for k in range(3):
        if h <= _STOPS[k + 1]:
            f = (h - _STOPS[k]) / (_STOPS[k + 1] - _STOPS[k])
            r, g, b = (round(x + (y - x) * f) for x, y in zip(rgb[k], rgb[k + 1], strict=True))
            return f"#{r:02x}{g:02x}{b:02x}"
    return EMBER[-1]


def pose(t: float) -> tuple[list[tuple[float, float]], list[float]]:
    """Los puntos y su calor en el instante `t` (s) de la entrada; t ≥ DURATION_S: la palabra."""
    from fogar_gateway.cli.linea_viva_datos import BASE, WORD

    n = len(WORD)
    x0, x1 = min(p[0] for p in WORD), max(p[0] for p in WORD)
    y0, y1 = min(p[1] for p in WORD), max(p[1] for p in WORD)
    amp = (y1 - y0) * 0.05
    pts: list[tuple[float, float]] = []
    heat: list[float] = []
    for i, (wx, wy) in enumerate(WORD):
        lx = x0 + (x1 - x0) * i / (n - 1)
        if t < 0.8:  # la recta entera, con una ola que la cruza y un filamento de luz
            wave = math.sin(4 * math.pi * (lx - x0) / (x1 - x0) - t * 9) * amp * _smooth(_clamp(t / 0.25))
            pts.append((lx, BASE + wave))
            heat.append(math.exp(-(((i / n) - t / 0.8) / 0.06) ** 2))
        elif t < 1.1:  # cada punto se coloca bajo su letra, aún en la línea base
            e = _smooth((t - 0.8) / 0.3)
            pts.append((lx + (wx - lx) * e, BASE))
            heat.append(0.0)
        else:  # se escribe: sube de izquierda a derecha, con la brasa en el frente
            u = _clamp((t - 1.1) / 1.3)
            e = _smooth(_clamp((u - 0.45 * (wx - x0) / (x1 - x0)) / 0.55))
            pts.append((wx, BASE + (wy - BASE) * e))
            heat.append(math.sin(math.pi * e) if 0 < e < 1 else 0.0)
    return pts, heat


def render(t: float, cols: int = COLS, color: bool = True) -> Any:
    """Un fotograma como `rich.text.Text`: rejilla Braille de `cols` celdas de ancho."""
    from rich.text import Text

    from fogar_gateway.cli.linea_viva_datos import WORD

    pts, heat = pose(t)
    x0, x1 = min(p[0] for p in WORD), max(p[0] for p in WORD)
    y0, y1 = min(p[1] for p in WORD), max(p[1] for p in WORD)
    s = (cols * 2 - 2) / (x1 - x0)  # deja sitio al pincel de 2 puntos
    rows = int((y1 - y0) * s + 1) // 4 + 1
    grid = [[0] * cols for _ in range(rows)]
    hot = [[0.0] * cols for _ in range(rows)]
    for i in range(len(pts) - 1):
        (ax, ay), (bx, by) = pts[i], pts[i + 1]
        for k in range(4):
            x = round((ax + (bx - ax) * k / 4 - x0) * s)
            y = round((ay + (by - ay) * k / 4 - y0) * s)
            for dx, dy in _BRUSH:
                px, py = x + dx, y + dy
                if 0 <= px < cols * 2 and 0 <= py < rows * 4:
                    grid[py // 4][px // 2] |= _DOT[py % 4][px % 2]
                    hot[py // 4][px // 2] = max(hot[py // 4][px // 2], heat[i])
    out = Text(no_wrap=True)
    for r in range(rows):
        if r:
            out.append("\n")
        for c in range(cols):
            h = hot[r][c]
            out.append(chr(0x2800 + grid[r][c]), style=ember(h) if color and h > 0.08 else "")
    return out


def reduced_motion() -> bool:
    return bool(os.environ.get("FOGAR_REDUCED_MOTION"))


def play(console: Any) -> None:
    """La entrada animada en `console` (una terminal con color); con FOGAR_REDUCED_MOTION, la
    palabra quieta. En una terminal estrecha no pinta nada: la palabra no cabría."""
    from rich.live import Live

    if console.width < MIN_COLS:
        return
    if reduced_motion():
        console.print(render(DURATION_S))
        return
    with Live(console=console, auto_refresh=False) as live:
        start = time.monotonic()
        while (t := time.monotonic() - start) < DURATION_S:
            live.update(render(t), refresh=True)
            time.sleep(1 / FPS)
        live.update(render(DURATION_S), refresh=True)
    sys.stdout.flush()
