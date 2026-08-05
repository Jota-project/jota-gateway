# tests/integration/test_rest_openai.py
from src.core.config import settings
from src.main import app
from src.services.protocol import OrchestratorEvent
from tests.integration.conftest import CLIENT_ID


def test_get_models_returns_list(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "jota-gateway"


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
    from src.core.session_key import make_session_key

    captured = {}

    async def _stream(text, user_id, model_id=None, session_key=None):
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


def test_non_streaming_turn_conflict_returns_409(client, mock_orchestrator):
    """Issue #99: a rejected duplicate-session_key turn must map to HTTP 409,
    distinguishable from a generic orchestrator failure (502)."""
    from src.services.openclaw.registry import TURN_IN_PROGRESS_ERROR

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
    import src.services.pipeline_tracker as pt_module

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
    client_untrusted, ha_bearer_headers, mock_registry
):
    from src.core.session_key import make_session_key

    captured = {}

    async def _stream(text, user_id, model_id=None, session_key=None):
        captured["session_key"] = session_key
        captured["user_id"] = user_id
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

    expected = make_session_key("openclaw", CLIENT_ID)
    assert captured.get("session_key") == expected
    assert captured.get("user_id") == CLIENT_ID


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

    from src.db.models import ClientRecord

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
