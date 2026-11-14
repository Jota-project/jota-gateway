"""La tabla de códigos de cierre (`models/close_codes.py`) es el contrato con los clientes (§13).

Vigila que no derive: códigos únicos y en su rango (49xx = se reintenta), ningún sitio de cierre
fuera de `send_close` ni con un número o un texto propio, cada entrada usada en `src` (por el
AST, no por texto), documentada en docs/client-protocol.md, comprobada en el wire por algún
test (código y reason, tal como los recibe el cliente) y copiada tal cual en el panel web.
"""

import ast
import re
from pathlib import Path
from typing import get_args

import pytest

from fogar_gateway.api.routes import AGENT_CLOSE
from fogar_gateway.core.agent_policy import AgentPolicyKind
from fogar_gateway.models.close_codes import (
    ALL_CLOSES,
    MAX_REASON_BYTES,
    RETRYABLE_APPLICATION_CODES,
    Close,
    close_sent,
    send_close,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "fogar_gateway"
DOCS = ROOT / "docs" / "client-protocol.md"
WEB_CODES = ROOT / "web" / "src" / "lib" / "closeCodes.ts"
# Estándar con significado de transporte; el resto es de aplicación (4000-4999).
STANDARD = {1000, 1011, 1013}
# Nombres con los que el gateway guarda el WebSocket del cliente.
CLIENT_WS_NAMES = {"websocket", "client_ws"}


def _src_trees():
    for path in sorted(SRC.rglob("*.py")):
        if path.name != "close_codes.py":
            yield path, ast.parse(path.read_text(), str(path))


def test_codes_are_unique_except_the_shared_standard_ones():
    application = [f.code for f in ALL_CLOSES.values() if f.code not in STANDARD]
    assert len(application) == len(set(application))
    assert all(4000 <= c <= 4999 for c in application)
    pairs = [(f.code, f.reason) for f in ALL_CLOSES.values()]
    assert len(pairs) == len(set(pairs))
    # Un código, una clase de reconexión (los 1011 comparten la suya).
    by_code: dict[int, set[str]] = {}
    for f in ALL_CLOSES.values():
        by_code.setdefault(f.code, set()).add(f.reconnect)
    assert all(len(classes) == 1 for classes in by_code.values())


def test_the_range_says_whether_to_reconnect():
    """4900-4999 se reintenta con espera; 4000-4899 nunca se reconecta solo (§13)."""
    for name, f in ALL_CLOSES.items():
        if f.code in RETRYABLE_APPLICATION_CODES:
            assert f.reconnect == "backoff", name
        elif 4000 <= f.code <= 4999:
            assert f.reconnect in {"fix", "never_auto"}, name


@pytest.mark.parametrize("name", sorted(ALL_CLOSES))
def test_reason_fits_a_close_frame_and_is_ascii(name):
    reason = ALL_CLOSES[name].reason
    assert reason and reason.isascii()
    assert len(reason.encode()) <= MAX_REASON_BYTES


def test_application_codes_are_grouped():
    groups = {
        "HANDSHAKE_TIMEOUT": 49,
        "HANDSHAKE_": 40,
        "INVALID_CREDENTIAL": 40,
        "AGENT_": 40,
        "CONVERSATION_": 41,
        "REVOKED": 42,
    }
    for name, frame in ALL_CLOSES.items():
        group = next((g for prefix, g in groups.items() if name.startswith(prefix)), None)
        if group is not None:
            assert frame.code // 100 == group, name


def _close_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
        ):
            yield node


def _receiver_name(call: ast.Call) -> str | None:
    target = call.func.value  # type: ignore[attr-defined]
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def test_no_close_site_carries_its_own_code_or_reason():
    """Ningún `close(...)` con un código o un texto literal (salvo `ws.close(1000)` posicional de
    los sockets de subida, TTS y transcriptor, que no van al cliente)."""
    offenders = []
    for path, tree in _src_trees():
        for call in _close_calls(tree):
            literal_kw = [
                k.arg
                for k in call.keywords
                if k.arg in {"code", "reason"} and isinstance(k.value, ast.Constant)
            ]
            literal_pos = [
                a.value
                for a in call.args
                if isinstance(a, ast.Constant) and type(a.value) is int and a.value != 1000
            ]
            if literal_kw or literal_pos:
                offenders.append(f"{path.relative_to(ROOT)}:{call.lineno}")
    assert offenders == []


def test_client_websockets_are_closed_only_through_send_close():
    """`websocket.close(...)`/`client_ws.close(...)`, con código o sin él, solo en `send_close`:
    así cada cierre es de la tabla y nunca salen dos."""
    offenders = [
        f"{path.relative_to(ROOT)}:{call.lineno}"
        for path, tree in _src_trees()
        for call in _close_calls(tree)
        if _receiver_name(call) in CLIENT_WS_NAMES
    ]
    assert offenders == []


def test_every_close_is_used_by_the_gateway():
    """`Close.X` en código (AST), no en un docstring o un comentario."""
    used = {
        node.attr
        for _, tree in _src_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Close"
    }
    assert sorted(set(ALL_CLOSES) - used) == []
    assert sorted(used - set(ALL_CLOSES)) == []


def test_every_agent_policy_kind_has_its_close():
    assert set(AGENT_CLOSE) == set(get_args(AgentPolicyKind))


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


def test_the_web_panel_copy_matches_the_table():
    """`web/src/lib/closeCodes.ts` (código → clase de reconexión) es copia exacta de la tabla, y
    su regla para los códigos que no conoce usa el mismo rango reintentable."""
    text = WEB_CODES.read_text()
    block = re.search(r"export const CLOSE_CODES = \{(.*?)\n\}", text, re.S)
    assert block is not None
    web = {int(c): r for c, r in re.findall(r"^\s*(\d{4}): '(\w+)',", block.group(1), re.M)}
    gateway = {f.code: f.reconnect for f in ALL_CLOSES.values()}
    assert web == gateway
    first, last = RETRYABLE_APPLICATION_CODES[0], RETRYABLE_APPLICATION_CODES[-1]
    assert f"code >= {first} && code <= {last}) return 'backoff'" in text
    assert f"code >= 4000 && code <= {first - 1}) return 'fix'" in text


class _WS:
    def __init__(self):
        self.scope: dict = {}
        self.closes: list = []

    async def close(self, code=1000, reason=None):
        self.closes.append((code, reason))


async def test_send_close_never_sends_two():
    ws = _WS()
    assert close_sent(ws) is None
    assert await send_close(ws, Close.REVOKED) is True
    assert await send_close(ws, Close.NORMAL) is False
    assert ws.closes == [(4200, "credential revoked")]
    assert close_sent(ws) == Close.REVOKED
