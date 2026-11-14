# tests/integration/test_rest_openai.py
import json
import logging

from sqlmodel import Session

from fogar_gateway.core.config import settings
from fogar_gateway.db.models import ClientRecord
from fogar_gateway.main import app
from fogar_gateway.services.protocol import OrchestratorEvent
from tests.integration.conftest import CLIENT_ID, default_conversation_key


def test_get_models_returns_list(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "fogar-gateway"


def test_chat_completions_non_streaming_uses_orchestrator(client, mock_orchestrator):
    """Always routes through the orchestrator — no LLM bypass."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "Hola"


def test_chat_completions_uses_correct_session_key(client, mock_registry, mock_orchestrator):
    """session_key passed to stream_response matches agent:{default_agent}:ha."""
    from fogar_gateway.core.session_key import make_session_key

    captured = {}

    async def _stream(text, session_key=None):
        captured["session_key"] = session_key
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "test"}],
            "stream": False,
        },
    )

    # default_agent_id from GatewayInfo mock is "main"
    expected = make_session_key("main", "ha")
    assert captured.get("session_key") == expected


def test_chat_completions_uses_last_user_message(client):
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [
                {"role": "user", "content": "First"},
                {"role": "assistant", "content": "Answer"},
                {"role": "user", "content": "Second"},
            ],
            "stream": False,
        },
    )
    assert r.status_code == 200


def test_chat_completions_streaming_returns_sse(client):
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        r.read()
        body = r.text
    assert "data:" in body
    assert "[DONE]" in body


def _parse_sse_tokens(body: str) -> list[str]:
    """Extrae el contenido de cada delta token de un cuerpo SSE."""
    import json as _json

    tokens = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame.startswith("data:") or "[DONE]" in frame:
            continue
        try:
            data = _json.loads(frame[len("data: ") :])
            content = data["choices"][0]["delta"].get("content")
            if content:
                tokens.append(content)
        except (KeyError, IndexError, _json.JSONDecodeError):
            pass
    return tokens


def _parse_sse_payloads(body: str) -> list[dict]:
    """Parsea todos los frames SSE JSON y omite el sentinel [DONE]."""
    import json as _json

    payloads = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame.startswith("data: ") or frame == "data: [DONE]":
            continue
        payloads.append(_json.loads(frame[len("data: ") :]))
    return payloads


def test_streaming_each_token_maps_to_separate_sse_frame(client, mock_orchestrator):
    """Cada token del orquestador debe aparecer como un SSE frame separado con su contenido."""

    async def _stream(*args, **kwargs):
        yield OrchestratorEvent(type="token", content="Hola")
        yield OrchestratorEvent(type="token", content=" mundo")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "test"}],
            "stream": True,
        },
    ) as r:
        r.read()
        body = r.text

    tokens = _parse_sse_tokens(body)
    assert tokens == ["Hola", " mundo"], f"Tokens incorrectos en el SSE: {tokens}"


def test_streaming_stop_frame_has_finish_reason_stop(client, mock_orchestrator):
    """El último frame antes de [DONE] debe tener finish_reason: stop."""
    import json as _json

    async def _stream(*args, **kwargs):
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "test"}],
            "stream": True,
        },
    ) as r:
        r.read()
        body = r.text

    frames = [
        f.strip() for f in body.split("\n\n") if f.strip().startswith("data:") and "[DONE]" not in f
    ]
    stop_frames = []
    for frame in frames:
        try:
            data = _json.loads(frame[len("data: ") :])
            if data["choices"][0].get("finish_reason") == "stop":
                stop_frames.append(data)
        except (KeyError, IndexError, _json.JSONDecodeError):
            pass

    assert len(stop_frames) == 1, "Debe haber exactamente un frame con finish_reason: stop"


def test_streaming_session_is_completed_after_response(client, mock_orchestrator):
    """La sesión HTTP debe estar marcada como 'completed' al terminar el stream."""
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "test"}],
            "stream": True,
        },
    ) as r:
        r.read()

    sessions = app.state.session_registry.get_all()
    http_sessions = [s for s in sessions if s.session_id.startswith("http:")]
    assert http_sessions, "Debe haber al menos una sesión HTTP registrada"
    assert http_sessions[0].status == "completed", (
        f"La sesión debe estar 'completed', está '{http_sessions[0].status}'"
    )


def test_non_streaming_orchestrator_error_returns_502(client, mock_orchestrator):
    """Un error del orquestador en modo no-streaming debe devolver HTTP 502, no 200."""

    async def _error_stream(*args, **kwargs):
        yield OrchestratorEvent(type="error", content="orchestrator_unavailable")

    mock_orchestrator.stream_response = _error_stream

    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": False,
        },
    )

    assert r.status_code == 502, f"Esperado 502, recibido {r.status_code}: {r.text}"


def test_streaming_orchestrator_error_before_first_token_is_terminal_error(
    client,
    mock_orchestrator,
):
    async def _error_stream(*args, **kwargs):
        yield OrchestratorEvent(type="error", content="orchestrator_unavailable")

    mock_orchestrator.stream_response = _error_stream

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": True,
        },
    ) as response:
        response.read()
        body = response.text

    payloads = _parse_sse_payloads(body)
    assert payloads[-2] == {
        "error": {
            "message": "orchestrator_unavailable",
            "type": "server_error",
        },
    }
    assert payloads[-1]["choices"][0]["finish_reason"] == "error"
    assert not any(
        payload.get("choices", [{}])[0].get("finish_reason") == "stop" for payload in payloads
    )
    assert body.rstrip().endswith("data: [DONE]")  # #217: [DONE] también ante error

    http_sessions = [
        session
        for session in app.state.session_registry.get_all()
        if session.session_id.startswith("http:")
    ]
    assert http_sessions[-1].status == "error"


def test_streaming_orchestrator_error_after_token_preserves_partial_output(
    client,
    mock_orchestrator,
):
    async def _partial_error_stream(*args, **kwargs):
        yield OrchestratorEvent(type="token", content="respuesta parcial")
        yield OrchestratorEvent(type="error", content="orchestrator_unavailable")

    mock_orchestrator.stream_response = _partial_error_stream

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": True,
        },
    ) as response:
        response.read()
        body = response.text

    payloads = _parse_sse_payloads(body)
    assert _parse_sse_tokens(body) == ["respuesta parcial"]
    assert payloads[-2] == {
        "error": {
            "message": "orchestrator_unavailable",
            "type": "server_error",
        },
    }
    assert payloads[-1]["choices"][0]["finish_reason"] == "error"
    assert not any(
        payload.get("choices", [{}])[0].get("finish_reason") == "stop" for payload in payloads
    )
    assert body.rstrip().endswith("data: [DONE]")  # #217: [DONE] también ante error

    http_sessions = [
        session
        for session in app.state.session_registry.get_all()
        if session.session_id.startswith("http:")
    ]
    assert http_sessions[-1].status == "error"


def test_non_streaming_turn_timeout_returns_502(client, mock_orchestrator):
    """#115: a turn_timeout error from the orchestrator maps to 502 exactly
    like any other orchestrator error — it must not hang the HTTP response."""

    async def _timeout_stream(*args, **kwargs):
        yield OrchestratorEvent(type="error", content="turn_timeout")

    mock_orchestrator.stream_response = _timeout_stream

    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": False,
        },
    )

    assert r.status_code == 502, f"Esperado 502, recibido {r.status_code}: {r.text}"


def test_streaming_turn_timeout_is_terminal_error(client, mock_orchestrator):
    """#115: same as above, streaming mode — reuses #111's error-frame path."""

    async def _timeout_stream(*args, **kwargs):
        yield OrchestratorEvent(type="error", content="turn_timeout")

    mock_orchestrator.stream_response = _timeout_stream

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": True,
        },
    ) as response:
        response.read()
        body = response.text

    payloads = _parse_sse_payloads(body)
    assert payloads[-2] == {
        "error": {
            "message": "turn_timeout",
            "type": "server_error",
        },
    }
    assert payloads[-1]["choices"][0]["finish_reason"] == "error"
    assert body.rstrip().endswith("data: [DONE]")  # #217: [DONE] también ante error


def test_non_streaming_turn_conflict_returns_409(client, mock_orchestrator):
    """Issue #99: a rejected duplicate-session_key turn must map to HTTP 409,
    distinguishable from a generic orchestrator failure (502)."""
    from fogar_gateway.services.openclaw.registry import TURN_IN_PROGRESS_ERROR

    async def _conflict_stream(*args, **kwargs):
        yield OrchestratorEvent(type="error", content=TURN_IN_PROGRESS_ERROR)

    mock_orchestrator.stream_response = _conflict_stream

    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": False,
        },
    )

    assert r.status_code == 409, f"Esperado 409, recibido {r.status_code}: {r.text}"


def test_streaming_done_delivered_when_tracker_close_raises(client, mock_orchestrator, monkeypatch):
    """[DONE] debe llegar aunque tracker.close() lance una excepción.

    Sin el fix (sentinel después de close), si close() lanza, el generador
    se queda bloqueado en queue.get() y [DONE] nunca llega — test colgaría.
    """
    import fogar_gateway.services.pipeline_tracker as pt_module

    async def _raising_close(self, status="completed"):
        raise RuntimeError("tracker close failed intentionally")

    monkeypatch.setattr(pt_module.PipelineTracker, "close", _raising_close)

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "test"}],
            "stream": True,
        },
    ) as r:
        r.read()
        body = r.text

    assert "[DONE]" in body, f"[DONE] debe llegar aunque tracker.close() falle: {body!r}"


def test_invalid_messages_type_returns_422(client):
    """messages como string (no lista) debe devolver 422, no 500."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": "not a list",
        },
    )
    assert r.status_code == 422, f"Esperado 422, recibido {r.status_code}: {r.text}"


def test_missing_body_returns_422(client):
    """Body completamente ausente debe devolver 422."""
    r = client.post(
        "/v1/chat/completions", content=b"", headers={"content-type": "application/json"}
    )
    assert r.status_code == 422, f"Esperado 422, recibido {r.status_code}: {r.text}"


def test_chat_completions_no_user_message_returns_empty(client):
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "system", "content": "Be helpful"}],
            "stream": False,
        },
    )
    assert r.status_code == 200


def test_http_session_appears_in_registry(client):
    """After an HTTP call, a session record appears in app.state.session_registry."""
    client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
        },
    )
    sessions = app.state.session_registry.get_all()
    http_sessions = [s for s in sessions if s.session_id.startswith("http:")]
    assert len(http_sessions) >= 1
    assert http_sessions[0].client_id == "ha"


def test_get_models_from_trusted_loopback_passes(client):
    r = client.get("/v1/models")
    assert r.status_code == 200


def test_get_models_from_untrusted_origin_without_auth_returns_401(client_untrusted):
    r = client_untrusted.get("/v1/models")
    assert r.status_code == 401


def test_chat_completions_from_untrusted_origin_without_auth_returns_401(client_untrusted):
    r = client_untrusted.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": False,
        },
    )
    assert r.status_code == 401


def test_chat_completions_from_untrusted_origin_with_valid_key_passes(
    client_untrusted, ha_bearer_headers
):
    r = client_untrusted.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": False,
        },
        headers=ha_bearer_headers,
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hola"


def test_chat_completions_with_valid_key_uses_real_client_session_key(
    client_untrusted, ha_bearer_headers, mock_registry, db_engine
):
    captured = {}

    async def _stream(text, session_key=None):
        captured["session_key"] = session_key
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_registry.stream_response = _stream

    client_untrusted.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "test"}],
            "stream": False,
        },
        headers=ha_bearer_headers,
    )

    # M19: la conversación por defecto de hoy del cliente para ese agente.
    expected = default_conversation_key(db_engine, "openclaw")
    assert expected.startswith("agent:openclaw:fogar-")
    assert captured.get("session_key") == expected


def test_chat_completions_with_invalid_key_returns_401_even_from_loopback(client):
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": False,
        },
        headers={"authorization": "Bearer not-a-real-key"},
    )
    assert r.status_code == 401


def test_chat_completions_with_inactive_client_key_returns_401(client, db_engine):
    from sqlmodel import Session

    from fogar_gateway.db.models import ClientRecord

    with Session(db_engine) as s:
        s.add(
            ClientRecord(
                id="inactive-client",
                name="Inactive",
                client_key="inactive-key",
                is_active=False,
            )
        )
        s.commit()

    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": False,
        },
        headers={"authorization": "Bearer inactive-key"},
    )
    assert r.status_code == 401


def test_chat_completions_x_real_ip_alone_does_not_grant_trust(client, monkeypatch):
    """Peer is 127.0.0.1 (a trusted proxy), so X-Real-IP is read — but the IP it
    names (203.0.113.9) still isn't in TRUSTED_NETWORKS, so it must still fail."""
    monkeypatch.setattr(settings, "TRUSTED_NETWORKS", "")
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": False,
        },
        headers={"x-real-ip": "203.0.113.9"},
    )
    assert r.status_code == 401


def test_chat_completions_trusts_x_real_ip_within_trusted_networks(client, monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_NETWORKS", "192.168.50.0/24")
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": False,
        },
        headers={"x-real-ip": "192.168.50.7"},
    )
    assert r.status_code == 200


def test_chat_completions_ignores_x_real_ip_from_untrusted_peer(client_untrusted):
    """client_untrusted's peer (203.0.113.5) is not in TRUSTED_PROXIES, so a
    spoofed X-Real-IP claiming to be loopback must be ignored."""
    r = client_untrusted.post(
        "/v1/chat/completions",
        json={
            "model": "openclaw",
            "messages": [{"role": "user", "content": "Hola"}],
            "stream": False,
        },
        headers={"x-real-ip": "127.0.0.1"},
    )
    assert r.status_code == 401


def test_non_streaming_orchestrator_error_records_session_as_error(client, mock_orchestrator):
    """Hallazgo 4 de la auditoría: un no-streaming fallido no queda como 'completed'."""

    async def _error_stream(*args, **kwargs):
        yield OrchestratorEvent(type="error", content="orchestrator_unavailable")

    mock_orchestrator.stream_response = _error_stream

    r = client.post(
        "/v1/chat/completions",
        json={"model": "openclaw", "messages": [{"role": "user", "content": "Hola"}]},
    )

    assert r.status_code == 502
    http_sessions = [
        s for s in app.state.session_registry.get_all() if s.session_id.startswith("http:")
    ]
    assert len(http_sessions) == 1
    assert http_sessions[0].status == "error"


# ── Alias de modelo `fogar-gateway` y errores no-RuntimeError ──────────────────

BODY = {"messages": [{"role": "user", "content": "Hola"}], "stream": False}


def _capture_session_key(mock_orchestrator):
    captured = {}

    async def _stream(text, session_key=None):
        captured["session_key"] = session_key
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream
    return captured


def test_model_alias_fogar_gateway_with_bearer_returns_200(
    client_untrusted, ha_bearer_headers, mock_orchestrator, db_engine
):
    captured = _capture_session_key(mock_orchestrator)
    r = client_untrusted.post(
        "/v1/chat/completions",
        json={**BODY, "model": "fogar-gateway"},
        headers=ha_bearer_headers,
    )
    assert r.status_code == 200, r.text
    # Sin agente explícito -> cascada: default de OpenClaw ("main").
    assert captured["session_key"] == default_conversation_key(db_engine, "main")


def test_model_alias_uses_client_default_agent_in_cascade(
    client_untrusted, ha_bearer_headers, mock_orchestrator, db_engine
):
    with Session(db_engine) as s:
        rec = s.get(ClientRecord, CLIENT_ID)
        rec.default_agent = "a"
        s.add(rec)
        s.commit()
    captured = _capture_session_key(mock_orchestrator)
    r = client_untrusted.post(
        "/v1/chat/completions",
        json={**BODY, "model": "fogar-gateway"},
        headers=ha_bearer_headers,
    )
    assert r.status_code == 200, r.text
    assert captured["session_key"] == default_conversation_key(db_engine, "a")


def test_model_alias_respects_allowed_agents_of_cascade_default(
    client_untrusted, ha_bearer_headers, mock_orchestrator, db_engine
):
    """El alias no es un bypass: la allowlist sigue aplicando al agente resuelto."""
    with Session(db_engine) as s:
        rec = s.get(ClientRecord, CLIENT_ID)
        rec.allowed_agents = '["a"]'
        s.add(rec)
        s.commit()
    r = client_untrusted.post(
        "/v1/chat/completions",
        json={**BODY, "model": "fogar-gateway"},
        headers=ha_bearer_headers,
    )
    assert r.status_code == 403
    assert r.json()["reason"] == "agent_not_permitted"


def test_real_agent_as_model_still_works_and_unknown_still_403(
    client_untrusted, ha_bearer_headers, mock_orchestrator, db_engine
):
    captured = _capture_session_key(mock_orchestrator)
    r = client_untrusted.post(
        "/v1/chat/completions", json={**BODY, "model": "a"}, headers=ha_bearer_headers
    )
    assert r.status_code == 200
    assert captured["session_key"] == default_conversation_key(db_engine, "a")

    r = client_untrusted.post(
        "/v1/chat/completions", json={**BODY, "model": "nope"}, headers=ha_bearer_headers
    )
    assert r.status_code == 403
    assert r.json()["reason"] == "agent_not_available"


def test_streaming_non_runtime_error_ends_as_error_with_done(client, mock_orchestrator):
    async def _boom(*args, **kwargs):
        yield OrchestratorEvent(type="token", content="parcial")
        raise ValueError("boom interno")

    mock_orchestrator.stream_response = _boom
    with client.stream(
        "POST", "/v1/chat/completions", json={**BODY, "model": "openclaw", "stream": True}
    ) as r:
        r.read()
        body = r.text

    payloads = _parse_sse_payloads(body)
    assert payloads[-1]["choices"][0]["finish_reason"] == "error"
    assert "error" in payloads[-2]
    assert "boom interno" not in body  # no filtrar detalles internos
    assert not any(p.get("choices", [{}])[0].get("finish_reason") == "stop" for p in payloads)
    assert body.rstrip().endswith("data: [DONE]")


def test_non_streaming_non_runtime_error_returns_502(client, mock_orchestrator):
    async def _boom(*args, **kwargs):
        raise ValueError("boom interno")
        yield  # pragma: no cover

    mock_orchestrator.stream_response = _boom
    r = client.post("/v1/chat/completions", json={**BODY, "model": "openclaw"})
    assert r.status_code == 502, r.text
    assert "boom interno" not in r.text


def test_non_runtime_error_message_never_reaches_logs_stream_and_non_stream(
    client, mock_orchestrator, caplog
):
    """Higiene de logs: solo el tipo de la excepción, nunca su mensaje (puede llevar texto
    de usuario), ni en el traceback a DEBUG."""
    import logging
    import traceback

    marker = "SECRET-USER-TEXT"
    caplog.set_level(logging.DEBUG)

    async def _boom(*args, **kwargs):
        raise ValueError(marker)
        yield  # pragma: no cover

    mock_orchestrator.stream_response = _boom
    r = client.post("/v1/chat/completions", json={**BODY, "model": "openclaw"})
    assert r.status_code == 502
    with client.stream(
        "POST", "/v1/chat/completions", json={**BODY, "model": "openclaw", "stream": True}
    ) as r2:
        r2.read()

    assert "ValueError" in caplog.text  # sí se registra el tipo
    assert marker not in caplog.text
    for rec in caplog.records:
        assert marker not in rec.getMessage()
        if rec.exc_info:
            assert marker not in "".join(traceback.format_exception(*rec.exc_info))


MARK = "SECRET-INTERNAL"


def _ids(r):
    return [m["id"] for m in r.json()["data"]]


# ── #234: /v1/models lista el roster ──────────────────────────────────────────


def test_models_lists_alias_and_roster_agents_for_bearer_caller(
    client_untrusted, ha_bearer_headers
):
    r = client_untrusted.get("/v1/models", headers=ha_bearer_headers)
    assert r.status_code == 200
    assert _ids(r) == ["fogar-gateway", "main", "a", "openclaw"]
    assert all(m["object"] == "model" for m in r.json()["data"])


def test_models_for_legacy_trusted_caller_lists_only_alias(client):
    """Origen confiable SIN Bearer: chat ignora body.model y usa el agente por defecto, así
    que no se anuncian agentes que no se podrían elegir."""
    assert _ids(client.get("/v1/models")) == ["fogar-gateway"]


def _chat_session_key(c, mock_orchestrator, model, headers=None):
    captured = {}

    async def _stream(text, session_key=None):
        captured["sk"] = session_key
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream
    r = c.post("/v1/chat/completions", json={**BODY, "model": model}, headers=headers or {})
    assert r.status_code == 200, r.text
    return captured["sk"]


def test_models_chat_coherence_bearer_every_listed_model_selects_that_agent(
    client_untrusted, ha_bearer_headers, mock_orchestrator
):
    for mid in _ids(client_untrusted.get("/v1/models", headers=ha_bearer_headers)):
        sk = _chat_session_key(client_untrusted, mock_orchestrator, mid, ha_bearer_headers)
        expected = "main" if mid == "fogar-gateway" else mid
        assert sk.split(":")[1] == expected


def test_models_chat_coherence_legacy_listed_model_is_what_chat_uses(
    client, mock_orchestrator
):
    listed = _ids(client.get("/v1/models"))
    assert listed == ["fogar-gateway"]
    # Un agente real no listado se ignora en la rama legacy: cae al agente por defecto.
    assert _chat_session_key(client, mock_orchestrator, "a").split(":")[1] == "main"
    assert _chat_session_key(client, mock_orchestrator, listed[0]).split(":")[1] == "main"


def test_models_respects_allowed_agents_of_authenticated_client(
    client_untrusted, ha_bearer_headers, db_engine
):
    with Session(db_engine) as s:
        rec = s.get(ClientRecord, CLIENT_ID)
        rec.allowed_agents = '["a"]'
        s.add(rec)
        s.commit()
    r = client_untrusted.get("/v1/models", headers=ha_bearer_headers)
    assert _ids(r) == ["fogar-gateway", "a"]


def test_models_with_empty_allowed_agents_lists_only_alias(
    client_untrusted, ha_bearer_headers, db_engine
):
    with Session(db_engine) as s:
        rec = s.get(ClientRecord, CLIENT_ID)
        rec.allowed_agents = "[]"
        s.add(rec)
        s.commit()
    r = client_untrusted.get("/v1/models", headers=ha_bearer_headers)
    assert _ids(r) == ["fogar-gateway"]


def test_models_without_allowed_agents_restriction_lists_full_roster(
    client_untrusted, ha_bearer_headers
):
    r = client_untrusted.get("/v1/models", headers=ha_bearer_headers)
    assert _ids(r) == ["fogar-gateway", "main", "a", "openclaw"]


def test_models_without_roster_lists_only_alias(client, mock_orchestrator):
    mock_orchestrator.gateway_info = None
    assert _ids(client.get("/v1/models")) == ["fogar-gateway"]


def test_models_with_empty_roster_lists_only_alias(client, mock_orchestrator):
    mock_orchestrator.gateway_info.agents = []
    assert _ids(client.get("/v1/models")) == ["fogar-gateway"]


def test_models_never_duplicates_the_alias(client_untrusted, ha_bearer_headers, mock_orchestrator):
    mock_orchestrator.gateway_info.agents.append("fogar-gateway")
    ids = _ids(client_untrusted.get("/v1/models", headers=ha_bearer_headers))
    assert ids.count("fogar-gateway") == 1 and len(ids) > 1


# ── #228: lo que ve un llamador de /v1 ────────────────────────────────────────


def test_orchestrator_error_event_code_reaches_v1_caller(client, mock_orchestrator):
    async def _stream(*a, **k):
        yield OrchestratorEvent(type="error", content="orchestrator_error")

    mock_orchestrator.stream_response = _stream
    r = client.post("/v1/chat/completions", json=BODY)
    assert r.status_code == 502
    assert r.json() == {"error": "orchestrator_error"}


def test_foreign_runtimeerror_text_never_reaches_v1_caller_nor_logs(
    client, mock_orchestrator, caplog
):
    caplog.set_level(logging.DEBUG, logger="fogar_gateway")

    async def _stream(*a, **k):
        raise RuntimeError(MARK)
        yield  # pragma: no cover

    mock_orchestrator.stream_response = _stream
    r = client.post("/v1/chat/completions", json=BODY)
    assert r.status_code == 502
    assert MARK not in r.text
    assert r.json() == {"error": "internal_error"}
    assert MARK not in "\n".join(
        x.getMessage() for x in caplog.records if x.name.startswith("fogar_gateway")
    )


def test_foreign_runtimeerror_text_never_reaches_v1_stream(client, mock_orchestrator, caplog):
    caplog.set_level(logging.DEBUG, logger="fogar_gateway")

    async def _stream(*a, **k):
        raise RuntimeError(MARK)
        yield  # pragma: no cover

    mock_orchestrator.stream_response = _stream
    r = client.post("/v1/chat/completions", json={**BODY, "stream": True})
    assert MARK not in r.text
    payloads = _parse_sse_payloads(r.text)
    assert any(
        isinstance(p, dict) and p.get("error", {}).get("message") == "internal_error"
        for p in payloads
    ), json.dumps(payloads)
    assert MARK not in "\n".join(
        x.getMessage() for x in caplog.records if x.name.startswith("fogar_gateway")
    )
