"""#130 S2: la política de cliente lento necesita el write flow control de sansio.

uvicorn < 0.52.1 no define pause_writing/resume_writing en el protocolo sansio: ahí
`send` nunca bloquea y el buffer del transporte crece sin límite, así que el timeout
por envío no detectaría nada.
"""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version
from uvicorn.protocols.websockets.websockets_sansio_impl import WebSocketsSansIOProtocol

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_sansio_protocol_applies_write_backpressure():
    # GUARD: pasa con el uvicorn instalado (>= 0.52.1); falla si cambia de implementación.
    assert hasattr(WebSocketsSansIOProtocol, "pause_writing")
    assert hasattr(WebSocketsSansIOProtocol, "resume_writing")


def test_pyproject_requires_a_uvicorn_with_write_backpressure():
    deps = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    spec = next(d for d in deps if Requirement(d).name == "uvicorn")
    floors = [
        Version(s.version) for s in Requirement(spec).specifier if s.operator in (">=", "==", "~=")
    ]
    assert floors and max(floors) >= Version("0.52.1"), spec
