def _req(method: str, req_id: str, params: dict) -> dict:
    return {"type": "req", "id": req_id, "method": method, "params": params}


# ── Conexión ──────────────────────────────────────────────────────


def connect_backend(req_id: str, token: str) -> dict:
    return _req(
        "connect",
        req_id,
        {
            "minProtocol": 3,
            "maxProtocol": 4,
            "client": {
                "id": "gateway-client",
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


# ── Agentes ───────────────────────────────────────────────────────


def agents_list(req_id: str) -> dict:
    return _req("agents.list", req_id, {})
