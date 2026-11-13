from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from fogar_gateway.services.openclaw.device import clean_scopes

ADMIN_SCOPE = "operator.admin"

AuthMethod = Literal["shared_token", "device_token"]
# unknown: no hay conexión ahora (aún no ha conectado, falló o se cayó); not_required: conectado
# sin emparejar (camino local de gateway-client/backend con el token compartido); paired: con
# token de dispositivo; pending: OpenClaw espera la aprobación; identity_required: OpenClaw pide
# identidad de dispositivo y el gateway no pudo mandarla (`identity_error` dice por qué).
PairingState = Literal["unknown", "not_required", "paired", "pending", "identity_required"]
# Por qué no se mandó la identidad: el fichero no vale o no se puede crear, o el reto no trae nonce.
IdentityError = Literal["identity_file_invalid", "challenge_without_nonce"]


def approve_command(request_id: str | None) -> str:
    """El comando que aprueba el gateway en la máquina de OpenClaw (sin id: `--latest` solo
    enseña la última petición e imprime el comando exacto)."""
    return f"openclaw devices approve {request_id or '--latest'}"


def hello_scopes(payload: dict) -> list[str] | None:
    """`hello-ok.auth.scopes` saneados; None si no vienen (desconocidos, no «ninguno»)."""
    auth = payload.get("auth")
    scopes = auth.get("scopes") if isinstance(auth, dict) else None
    return clean_scopes(scopes) if isinstance(scopes, list) else None


@dataclass
class GatewayInfo:
    default_agent_id: str
    tick_interval_ms: int
    max_payload: int
    # Agent ids in the order agents.list returns them (/v1/models lists them as-is).
    agents: list[str] = field(default_factory=list)
    # Permisos concedidos a esta conexión (`hello-ok.auth.scopes`), saneados; None si el
    # servidor no los anuncia (desconocidos).
    scopes: list[str] | None = None

    @property
    def admin(self) -> bool:
        return ADMIN_SCOPE in (self.scopes or [])

    def has_agent(self, agent_id: str) -> bool:
        return agent_id in self.agents

    def update_agents_from_list(self, payload: dict) -> None:
        """Populate agents from an agents.list response (the only roster source since
        OpenClaw server 2026.6.11, the minimum supported)."""
        self.agents = [a["id"] for a in payload.get("agents", [])]
        self.default_agent_id = payload.get("defaultId", self.default_agent_id)

    @classmethod
    def from_hello_ok(cls, payload: dict) -> "GatewayInfo":
        policy = payload.get("policy", {})
        snapshot = payload.get("snapshot", {})
        return cls(
            default_agent_id=snapshot.get("sessionDefaults", {}).get("defaultAgentId") or "main",
            tick_interval_ms=policy.get("tickIntervalMs", 15000),
            max_payload=policy.get("maxPayload", 26214400),
            scopes=hello_scopes(payload),
        )


@dataclass
class OrchestratorAccess:
    """Con qué acceso está (o intenta estar) el gateway en OpenClaw *ahora*: lo enseñan
    `/admin/state`, `fogar-gw status` y `doctor`. Lo actualiza OpenClawClient en el sitio (nunca
    se reemplaza): al conectar, con PAIRING_REQUIRED, y vuelve a `unknown` (`reset()`) cuando un
    connect falla o la conexión se cae, para no enseñar como actual un acceso que ya no hay. Sin
    credenciales: el deviceId y el id de la petición de emparejamiento son públicos."""

    scopes: list[str] | None = None  # None: sin conexión, o conectado sin permisos anunciados
    auth_method: AuthMethod | None = None
    pairing: PairingState = "unknown"
    pairing_request_id: str | None = None
    identity_error: IdentityError | None = None
    device_id: str | None = None

    @property
    def admin(self) -> bool:
        return ADMIN_SCOPE in (self.scopes or [])

    def reset(self) -> None:
        """Sin conexión: todo a desconocido salvo el deviceId (sigue siendo el de la instalación)."""
        self.scopes = None
        self.auth_method = None
        self.pairing = "unknown"
        self.pairing_request_id = None
        self.identity_error = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "scopes": list(self.scopes) if self.scopes is not None else None,
            "admin": self.admin,
            "auth_method": self.auth_method,
            "pairing": self.pairing,
            "pairing_request_id": self.pairing_request_id,
            "identity_error": self.identity_error,
            "device_id": self.device_id,
        }


def _flatten_tool_result_content(content: list | None) -> str | None:
    """Join result.content[].text blocks from a session.tool 'result' payload.

    OpenClaw tool results carry a list of typed content blocks; fogar-gateway
    only cares about the text ones. Returns None if there's nothing to show.
    """
    if not content:
        return None
    texts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    joined = "\n".join(t for t in texts if t)
    return joined or None


@dataclass
class ToolCallEvent:
    phase: Literal["start", "result"]
    name: str
    tool_call_id: str
    args: dict | None = None
    result: str | None = None
    is_error: bool | None = None

    @classmethod
    def from_session_tool_payload(cls, data: dict) -> Optional["ToolCallEvent"]:
        """Parse the `data` sub-object of an OpenClaw `session.tool` event.

        Only `start` and `result` phases are recognized — `update` (streaming
        partial result) and anything else return None. Returns None on
        missing required fields (name, toolCallId) instead of raising.
        """
        phase = data.get("phase")
        name = data.get("name")
        tool_call_id = data.get("toolCallId")
        if phase not in ("start", "result") or not name or not tool_call_id:
            return None

        if phase == "result":
            result_payload = data.get("result") or {}
            return cls(
                phase="result",
                name=name,
                tool_call_id=tool_call_id,
                result=_flatten_tool_result_content(result_payload.get("content")),
                is_error=data.get("isError"),
            )

        return cls(
            phase="start",
            name=name,
            tool_call_id=tool_call_id,
            args=data.get("args"),
        )
