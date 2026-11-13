from fogar_gateway.services.openclaw.models import ADMIN_SCOPE


def _req(method: str, req_id: str, params: dict) -> dict:
    return {"type": "req", "id": req_id, "method": method, "params": params}


# ── Conexión ──────────────────────────────────────────────────────

# Lo que el gateway declara en `connect`; la firma del dispositivo (payload v3) usa los mismos
# valores. `gateway-client`/`backend` es también el valor por defecto del cliente de referencia
# de OpenClaw (`@openclaw/gateway-client`): docs/openclaw-protocol.md, «2026-11-13».
CLIENT_ID = "gateway-client"
CLIENT_MODE = "backend"
CLIENT_PLATFORM = "linux"
ROLE = "operator"
# Ningún método de admin se usa todavía (M19): se piden para tenerlos al emparejar.
OPERATOR_SCOPES = ("operator.read", "operator.write", ADMIN_SCOPE)


def connect_backend(
    req_id: str,
    token: str,
    *,
    scopes: tuple[str, ...] | list[str] = OPERATOR_SCOPES,
    device: dict | None = None,
    device_token: bool = False,
) -> dict:
    """`token` es el token compartido o, con `device_token=True`, el token de dispositivo, que
    entonces va en `auth.token` y en `auth.deviceToken`, como el cliente de referencia
    (`buildGatewayConnectAuth`). `device`: `{id, publicKey, signature, signedAt, nonce}`."""
    auth = {"token": token, "deviceToken": token} if device_token else {"token": token}
    params: dict = {
        "minProtocol": 3,
        "maxProtocol": 4,
        "client": {
            "id": CLIENT_ID,
            "displayName": "fogar-gateway",
            "version": "1.0.0",
            "platform": CLIENT_PLATFORM,
            "mode": CLIENT_MODE,
        },
        "role": ROLE,
        "scopes": list(scopes),
        "auth": auth,
    }
    if device is not None:
        params["device"] = device
    return _req("connect", req_id, params)


def sessions_subscribe(req_id: str) -> dict:
    return _req("sessions.subscribe", req_id, {})


def health(req_id: str) -> dict:
    return _req("health", req_id, {})


# ── Chat ──────────────────────────────────────────────────────────


def chat_send(req_id: str, session_key: str, message: str, idempotency_key: str) -> dict:
    return _req(
        "chat.send",
        req_id,
        {
            "sessionKey": session_key,
            "message": message,
            "idempotencyKey": idempotency_key,
        },
    )


def chat_abort(req_id: str, session_key: str) -> dict:
    return _req("chat.abort", req_id, {"sessionKey": session_key})


# ── Agentes ───────────────────────────────────────────────────────


def agents_list(req_id: str) -> dict:
    return _req("agents.list", req_id, {})
