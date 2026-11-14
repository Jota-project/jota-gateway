"""La tabla de códigos de cierre (`models/close_codes.py`) es el contrato con los clientes (§13).

Vigila que no derive: códigos únicos y en su rango, ningún sitio de cierre con un número o un
texto propio, cada entrada usada en `src`, documentada en docs/client-protocol.md y comprobada
en el wire por algún test (código y reason, tal como los recibe el cliente).
"""

import ast
import re
from pathlib import Path

import pytest

from fogar_gateway.models.close_codes import ALL_CLOSES, MAX_REASON_BYTES, Close, CloseFrame

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "fogar_gateway"
DOCS = ROOT / "docs" / "client-protocol.md"
# Estándar con significado de transporte; el resto es de aplicación (4000-4999).
STANDARD = {1000, 1011, 1013}


def test_codes_are_unique_except_the_shared_standard_ones():
    application = [f.code for f in ALL_CLOSES.values() if f.code not in STANDARD]
    assert len(application) == len(set(application))
    assert all(4000 <= c <= 4999 for c in application)
    pairs = [(f.code, f.reason) for f in ALL_CLOSES.values()]
    assert len(pairs) == len(set(pairs))


@pytest.mark.parametrize("name", sorted(ALL_CLOSES))
def test_reason_fits_a_close_frame_and_is_ascii(name):
    reason = ALL_CLOSES[name].reason
    assert reason and reason.isascii()
    assert len(reason.encode()) <= MAX_REASON_BYTES


def test_application_codes_are_grouped():
    groups = {
        "HANDSHAKE_": 40,
        "INVALID_CREDENTIAL": 40,
        "AGENT_": 40,
        "CONVERSATION_": 41,
        "REVOKED": 42,
    }
    for name, frame in ALL_CLOSES.items():
        for prefix, group in groups.items():
            if name.startswith(prefix):
                assert frame.code // 100 == group, name


def _close_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
        ):
            yield node


def test_no_close_site_carries_its_own_code_or_reason():
    """`close(code=…, reason=…)` (la forma de Starlette, la del cliente) solo con la tabla.

    Los `ws.close(1000)` posicionales son de sockets de subida (TTS, transcriptor): no van al
    cliente."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "close_codes.py":
            continue
        for call in _close_calls(ast.parse(path.read_text(), str(path))):
            literal_kw = [
                k.arg
                for k in call.keywords
                if k.arg in {"code", "reason"} and isinstance(k.value, ast.Constant)
            ]
            literal_pos = [
                a.value
                for a in call.args
                if isinstance(a, ast.Constant)
                and type(a.value) is int
                and a.value != 1000
            ]
            if literal_kw or literal_pos:
                offenders.append(f"{path.relative_to(ROOT)}:{call.lineno}")
    assert offenders == []


def test_every_close_is_used_by_the_gateway():
    source = "\n".join(
        p.read_text() for p in SRC.rglob("*.py") if p.name != "close_codes.py"
    )
    unused = [name for name in ALL_CLOSES if f"Close.{name}" not in source]
    assert unused == []


def test_every_close_is_documented():
    docs = DOCS.read_text()
    missing = [
        name
        for name, f in ALL_CLOSES.items()
        if not re.search(rf"\|\s*`?{f.code}`?\s*\|[^\n]*`{re.escape(f.reason)}`", docs)
    ]
    assert missing == []


def test_every_close_is_asserted_on_the_wire_by_some_test():
    """Cada entrada la recibe un cliente en algún test: `(código, "reason")` literal."""
    tests = "\n".join(
        p.read_text()
        for d in ("unit", "integration")
        for p in (ROOT / "tests" / d).rglob("test_*.py")
        if p.name != "test_close_codes.py"
    )
    unasserted = [
        name for name, f in ALL_CLOSES.items() if f'({f.code}, "{f.reason}")' not in tests
    ]
    assert unasserted == []


def test_close_frame_is_the_table_type():
    assert all(isinstance(v, CloseFrame) for v in ALL_CLOSES.values())
    assert Close.CONVERSATION_OPENED_ELSEWHERE.reconnect == "never_auto"
