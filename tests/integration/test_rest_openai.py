# tests/integration/test_rest_openai.py
from src.services.protocol import OrchestratorEvent
from src.main import app


def test_get_models_returns_list(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "jota-gateway"


def test_chat_completions_non_streaming_uses_orchestrator(client, mock_orchestrator):
    """Always routes through the orchestrator — no LLM bypass."""
    r = client.post("/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "user", "content": "Hola"}],
        "stream": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "Hola"


def test_chat_completions_uses_correct_session_key(client, mock_registry, mock_orchestrator):
    """session_key passed to stream_response matches agent:{default_agent}:ha."""
    from src.core.session_key import make_session_key
    captured = {}

    async def _stream(text, user_id, model_id=None, system_prompt_extra=None, session_key=None):
        captured["session_key"] = session_key
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    client.post("/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "user", "content": "test"}],
        "stream": False,
    })

    # default_agent_id from GatewayInfo mock is "main"
    expected = make_session_key("main", "ha")
    assert captured.get("session_key") == expected


def test_chat_completions_uses_last_user_message(client):
    r = client.post("/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Answer"},
            {"role": "user", "content": "Second"},
        ],
        "stream": False,
    })
    assert r.status_code == 200


def test_chat_completions_streaming_returns_sse(client):
    with client.stream("POST", "/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": True,
    }) as r:
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
            data = _json.loads(frame[len("data: "):])
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

    with client.stream("POST", "/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "user", "content": "test"}],
        "stream": True,
    }) as r:
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

    with client.stream("POST", "/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "user", "content": "test"}],
        "stream": True,
    }) as r:
        r.read()
        body = r.text

    frames = [f.strip() for f in body.split("\n\n") if f.strip().startswith("data:") and "[DONE]" not in f]
    stop_frames = []
    for frame in frames:
        try:
            data = _json.loads(frame[len("data: "):])
            if data["choices"][0].get("finish_reason") == "stop":
                stop_frames.append(data)
        except (KeyError, IndexError, _json.JSONDecodeError):
            pass

    assert len(stop_frames) == 1, "Debe haber exactamente un frame con finish_reason: stop"


def test_streaming_session_is_completed_after_response(client, mock_orchestrator):
    """La sesión HTTP debe estar marcada como 'completed' al terminar el stream."""
    with client.stream("POST", "/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "user", "content": "test"}],
        "stream": True,
    }) as r:
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

    r = client.post("/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "user", "content": "Hola"}],
        "stream": False,
    })

    assert r.status_code == 502, f"Esperado 502, recibido {r.status_code}: {r.text}"


def test_streaming_done_delivered_when_tracker_close_raises(client, mock_orchestrator, monkeypatch):
    """[DONE] debe llegar aunque tracker.close() lance una excepción.

    Sin el fix (sentinel después de close), si close() lanza, el generador
    se queda bloqueado en queue.get() y [DONE] nunca llega — test colgaría.
    """
    import src.services.pipeline_tracker as pt_module

    async def _raising_close(self, status="completed"):
        raise RuntimeError("tracker close failed intentionally")

    monkeypatch.setattr(pt_module.PipelineTracker, "close", _raising_close)

    with client.stream("POST", "/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "user", "content": "test"}],
        "stream": True,
    }) as r:
        r.read()
        body = r.text

    assert "[DONE]" in body, f"[DONE] debe llegar aunque tracker.close() falle: {body!r}"


def test_invalid_messages_type_returns_422(client):
    """messages como string (no lista) debe devolver 422, no 500."""
    r = client.post("/v1/chat/completions", json={
        "model": "openclaw",
        "messages": "not a list",
    })
    assert r.status_code == 422, f"Esperado 422, recibido {r.status_code}: {r.text}"


def test_missing_body_returns_422(client):
    """Body completamente ausente debe devolver 422."""
    r = client.post("/v1/chat/completions", content=b"", headers={"content-type": "application/json"})
    assert r.status_code == 422, f"Esperado 422, recibido {r.status_code}: {r.text}"


def test_chat_completions_no_user_message_returns_empty(client):
    r = client.post("/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "system", "content": "Be helpful"}],
        "stream": False,
    })
    assert r.status_code == 200


def test_http_session_appears_in_registry(client):
    """After an HTTP call, a session record appears in app.state.session_registry."""
    client.post("/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
    })
    sessions = app.state.session_registry.get_all()
    http_sessions = [s for s in sessions if s.session_id.startswith("http:")]
    assert len(http_sessions) >= 1
    assert http_sessions[0].client_id == "ha"
