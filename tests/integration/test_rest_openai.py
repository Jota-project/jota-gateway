# tests/integration/test_rest_openai.py
from src.services.orchestrators.protocol import OrchestratorEvent
from src.core.config import settings
from src.main import app


def test_get_models_returns_list(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "openclaw"


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

    expected = make_session_key(settings.OPENCLAW_DEFAULT_AGENT, "ha")
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
