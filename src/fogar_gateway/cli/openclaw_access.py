"""
openclaw_access.py
~~~~~~~~~~~~~~~~~~
Cómo se cuenta el acceso del gateway a OpenClaw (M19), igual en `fogar-gw status`, `doctor` y el
veredicto de `onboard` (`cli/verify.py`). Trabaja sobre el diccionario de
`OrchestratorAccess.as_dict()`, que es también `services.orchestrator` de `/admin/state`; un
gateway de otra versión puede no traer algún campo. Solo stdlib (más `models`, que también lo es).
"""

from typing import Any, Literal

from fogar_gateway.services.openclaw.models import approve_command

AUTH_LABELS = {"shared_token": "token compartido", "device_token": "token de dispositivo"}
IDENTITY_ERRORS = {
    "identity_file_invalid": "el fichero de identidad del gateway no vale o no se puede crear",
    "challenge_without_nonce": "este OpenClaw no manda nonce en el reto y no hay nada que firmar",
}
Level = Literal["ok", "info", "warn"]


def scopes_text(scopes: Any) -> str:
    """None: OpenClaw no los anunció (desconocidos), que no es lo mismo que ninguno."""
    if scopes is None:
        return "desconocidos"
    if isinstance(scopes, list) and scopes:
        return ", ".join(str(s) for s in scopes)
    return "ninguno"


def _how(access: dict[str, Any]) -> str:
    label = AUTH_LABELS.get(str(access.get("auth_method")), "?")
    return f"{label} · emparejado como dispositivo" if access.get("pairing") == "paired" else label


def pending_text(request_id: Any) -> str:
    rid = request_id if isinstance(request_id, str) else None
    return ("OpenClaw espera que apruebes este gateway como dispositivo. En la máquina de "
            f"OpenClaw: `{approve_command(rid)}`; el gateway se conecta solo al aprobarlo.")


def identity_required_text(cause: Any) -> str:
    why = IDENTITY_ERRORS.get(str(cause), "no hay identidad que mandar")
    return (f"OpenClaw exige identidad de dispositivo y el gateway no puede mandarla: {why}. "
            "Revísalo con `fogar-gw doctor`.")


def describe(access: dict[str, Any]) -> tuple[Level, str] | None:
    """Nivel y frase del acceso actual; None si ahora no hay conexión (`unknown`) o el gateway
    no da el dato."""
    pairing = access.get("pairing")
    if pairing == "pending":
        return "warn", pending_text(access.get("pairing_request_id"))
    if pairing == "identity_required":
        return "warn", identity_required_text(access.get("identity_error"))
    if pairing not in ("not_required", "paired"):
        return None
    how = _how(access)
    if access.get("admin"):
        return "ok", f"Con permisos de administración ({how})."
    if access.get("scopes") is None:
        return "info", f"Con permisos desconocidos: este OpenClaw no los anuncia ({how})."
    return "info", (f"Sin permisos de administración ({how}; permisos: "
                    f"{scopes_text(access.get('scopes'))}). Hoy no hacen falta para hablar.")


def short(access: dict[str, Any]) -> str | None:
    """Una línea para `fogar-gw status`; None si no hay conexión o el gateway no da el dato."""
    pairing = access.get("pairing")
    if pairing == "pending":
        rid = access.get("pairing_request_id")
        command = approve_command(rid if isinstance(rid, str) else None)
        return f"pendiente de aprobar: `{command}` en la máquina de OpenClaw"
    if pairing == "identity_required":
        return "sin identidad de dispositivo que mandar: `fogar-gw doctor`"
    if pairing not in ("not_required", "paired"):
        return None
    if access.get("admin"):
        grant = "admin"
    elif access.get("scopes") is None:
        grant = "permisos desconocidos"
    else:
        grant = "sin admin"
    paired = " · emparejado" if pairing == "paired" else ""
    return f"{grant} · {AUTH_LABELS.get(str(access.get('auth_method')), '?')}{paired}"
