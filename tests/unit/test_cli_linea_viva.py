"""La línea viva en la terminal y el final con estilo del asistente (#328)."""

import io

from rich.console import Console

from fogar_gateway.cli import linea_viva
from fogar_gateway.cli.linea_viva_datos import WORD
from fogar_gateway.cli.onboard import _qr
from fogar_gateway.cli.output import Out


def test_curve_has_the_brand_engine_points():
    assert len(WORD) == 600  # los 600 puntos del motor de fogar-brand, en el orden del trazo


def test_final_frame_is_the_still_word_in_ink():
    a = linea_viva.render(linea_viva.DURATION_S)
    b = linea_viva.render(99)
    assert a.plain == b.plain  # quieta: el último fotograma no cambia
    assert not [s for s in a.spans if s.style]  # tinta: sin brasa
    rows = a.plain.split("\n")
    assert len({len(r) for r in rows}) == 1 and len(rows[0]) == linea_viva.COLS
    assert all(0x2800 <= ord(c) <= 0x28FF for c in a.plain.replace("\n", ""))


def test_every_frame_keeps_the_same_size():
    sizes = {linea_viva.render(t).plain.count("\n") for t in (0, 0.4, 0.95, 1.6, 2.2)}
    assert len(sizes) == 1  # el bloque no salta de alto mientras se anima


def test_ember_only_while_it_moves():
    moving = linea_viva.render(1.6)
    assert {s.style for s in moving.spans} - {""}  # el frente que sube lleva brasa
    assert not linea_viva.render(1.6, color=False).spans


def test_ember_runs_through_the_four_brand_steps():
    assert linea_viva.ember(0) == linea_viva.EMBER[0]
    assert linea_viva.ember(1) == linea_viva.EMBER[-1]


def _console(width):
    return Console(file=io.StringIO(), width=width, force_terminal=True, color_system="truecolor")


def test_narrow_terminal_draws_nothing():
    c = _console(linea_viva.MIN_COLS - 1)
    linea_viva.play(c)
    assert c.file.getvalue() == ""


def test_reduced_motion_draws_the_still_word(monkeypatch):
    monkeypatch.setenv("FOGAR_REDUCED_MOTION", "1")
    c = _console(80)
    linea_viva.play(c)
    assert linea_viva.render(99).plain.split("\n")[0] in c.file.getvalue()


def test_qr_is_black_on_white_half_blocks():
    qr = _qr("https://fogar.example.ts.net/")
    assert {c for line in qr.plain.splitlines() for c in line.strip()} == {"▀"}
    assert {str(s.style) for s in qr.spans} <= {f"{a} on {b}" for a in ("#000000", "#ffffff")
                                               for b in ("#000000", "#ffffff")}


def test_steps_are_silent_without_style(capsys):
    out = Out(as_json=False)
    out.styled = False
    out.step("OpenClaw")
    out.ok("OpenClaw responde.")
    out.note("Una pista.")
    assert capsys.readouterr().out == "OpenClaw responde.\nUna pista.\n"  # la salida de siempre
