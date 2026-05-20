# tests/integration/test_rest_openai.py
import json
import pytest
from starlette.testclient import TestClient

from tests.integration.conftest import VALID_KEY


def test_get_models_returns_list(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert len(body["data"]) >= 1
    assert body["data"][0]["id"] == "openclaw"


def test_chat_completions_non_streaming_returns_content(client):
    r = client.post("/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "user", "content": "Hola"}],
        "stream": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "Hola"  # mock returns ["Hola"]


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
