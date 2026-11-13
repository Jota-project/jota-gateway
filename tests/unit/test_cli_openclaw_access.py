"""Una sola redacción del acceso a OpenClaw para status, doctor y onboard (revisión M19)."""

import pytest

from fogar_gateway.cli import openclaw_access as oa
from fogar_gateway.services.openclaw.models import OrchestratorAccess

ALL = ["operator.read", "operator.write", "operator.admin"]


def access(**kw) -> dict:
    a = OrchestratorAccess()
    for k, v in kw.items():
        setattr(a, k, v)
    return a.as_dict()


def test_scopes_text_distinguishes_unknown_from_none():
    assert oa.scopes_text(None) == "desconocidos"
    assert oa.scopes_text([]) == "ninguno"
    assert oa.scopes_text(["operator.read"]) == "operator.read"


@pytest.mark.parametrize(
    ("kw", "level", "text"),
    [
        ({"scopes": ALL, "auth_method": "shared_token", "pairing": "not_required"}, "ok",
         "Con permisos de administración (token compartido)."),
        ({"scopes": ALL, "auth_method": "device_token", "pairing": "paired"}, "ok",
         "Con permisos de administración (token de dispositivo · emparejado como dispositivo)."),
        ({"scopes": ["operator.read"], "auth_method": "shared_token", "pairing": "not_required"},
         "info", "Sin permisos de administración (token compartido; permisos: operator.read). "
         "Hoy no hacen falta para hablar."),
        ({"scopes": None, "auth_method": "shared_token", "pairing": "not_required"}, "info",
         "Con permisos desconocidos: este OpenClaw no los anuncia (token compartido)."),
    ],
)
def test_describe_connected(kw, level, text):
    assert oa.describe(access(**kw)) == (level, text)


def test_describe_pending_and_identity_required():
    level, text = oa.describe(access(pairing="pending", pairing_request_id="req-4"))
    assert level == "warn" and "`openclaw devices approve req-4`" in text
    level, text = oa.describe(access(pairing="identity_required",
                                     identity_error="identity_file_invalid"))
    assert level == "warn" and "fichero de identidad" in text and "approve" not in text


def test_unknown_or_old_gateway_says_nothing():
    assert oa.describe(access()) is None and oa.short(access()) is None
    assert oa.describe({"state": "CONNECTED"}) is None and oa.short({}) is None


@pytest.mark.parametrize(
    ("kw", "line"),
    [
        ({"scopes": ALL, "auth_method": "device_token", "pairing": "paired"},
         "admin · token de dispositivo · emparejado"),
        ({"scopes": ["operator.read"], "auth_method": "shared_token", "pairing": "not_required"},
         "sin admin · token compartido"),
        ({"scopes": None, "auth_method": "shared_token", "pairing": "not_required"},
         "permisos desconocidos · token compartido"),
        ({"pairing": "pending", "pairing_request_id": "req-1"},
         "pendiente de aprobar: `openclaw devices approve req-1` en la máquina de OpenClaw"),
        ({"pairing": "identity_required", "identity_error": "challenge_without_nonce"},
         "sin identidad de dispositivo que mandar: `fogar-gw doctor`"),
    ],
)
def test_short(kw, line):
    assert oa.short(access(**kw)) == line
