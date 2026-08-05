def _req(method: str, req_id: str, params: dict) -> dict:
    return {"type": "req", "id": req_id, "method": method, "params": params}


# ── Conexión ──────────────────────────────────────────────────────


def connect_backend(req_id: str, token: str, client_id: str = "gateway-client") -> dict:
    return _req(
        "connect",
        req_id,
        {
            "minProtocol": 3,
            "maxProtocol": 4,
            "client": {
                "id": client_id,
                "version": "1.0.0",
                "platform": "linux",
                "mode": "backend",
            },
            "role": "operator",
            "scopes": ["operator.read", "operator.write"],
            "auth": {"token": token},
        },
    )


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


def chat_history(req_id: str, session_key: str, limit: int = 50, offset: int = 0) -> dict:
    return _req(
        "chat.history",
        req_id,
        {
            "sessionKey": session_key,
            "limit": limit,
            "offset": offset,
        },
    )


def chat_inject(req_id: str, session_key: str, content: str) -> dict:
    return _req(
        "chat.inject",
        req_id,
        {
            "sessionKey": session_key,
            "content": content,
        },
    )


# ── Sessions ──────────────────────────────────────────────────────


def sessions_steer(req_id: str, session_key: str, steer_text: str) -> dict:
    return _req(
        "sessions.steer",
        req_id,
        {
            "key": session_key,
            "steerText": steer_text,
        },
    )


def sessions_list(req_id: str) -> dict:
    return _req("sessions.list", req_id, {})


# ── Agentes y modelos ─────────────────────────────────────────────


def agents_list(req_id: str) -> dict:
    return _req("agents.list", req_id, {})


def agent_identity_get(req_id: str, agent_id: str) -> dict:
    return _req("agent.identity.get", req_id, {"agentId": agent_id})


def models_list(req_id: str, view: str = "configured") -> dict:
    return _req("models.list", req_id, {"view": view})


def models_auth_status(req_id: str) -> dict:
    return _req("models.authStatus", req_id, {})
