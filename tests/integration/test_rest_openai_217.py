"""#217: alias de modelo `jota-gateway` (G-36) y errores no-RuntimeError en /v1 (G-37)."""

from sqlmodel import Session

from src.db.models import ClientRecord
from src.services.protocol import OrchestratorEvent
from tests.integration.conftest import CLIENT_ID
from tests.integration.test_rest_openai import _parse_sse_payloads

BODY = {"messages": [{"role": "user", "content": "Hola"}], "stream": False}


def _capture_session_key(mock_orchestrator):
    captured = {}

    async def _stream(text, user_id, model_id=None, session_key=None):
        captured["session_key"] = session_key
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream
    return captured


def test_model_alias_jota_gateway_with_bearer_returns_200(
    client_untrusted, ha_bearer_headers, mock_orchestrator
):
    from src.core.session_key import make_session_key

    captured = _capture_session_key(mock_orchestrator)
    r = client_untrusted.post(
        "/v1/chat/completions",
        json={**BODY, "model": "jota-gateway"},
        headers=ha_bearer_headers,
    )
    assert r.status_code == 200, r.text
    # Sin agente explícito -> cascada: default de OpenClaw ("main").
    assert captured["session_key"] == make_session_key("main", CLIENT_ID)


def test_model_alias_uses_client_default_agent_in_cascade(
    client_untrusted, ha_bearer_headers, mock_orchestrator, db_engine
):
    from src.core.session_key import make_session_key

    with Session(db_engine) as s:
        rec = s.get(ClientRecord, CLIENT_ID)
        rec.default_agent = "a"
        s.add(rec)
        s.commit()
    captured = _capture_session_key(mock_orchestrator)
    r = client_untrusted.post(
        "/v1/chat/completions",
        json={**BODY, "model": "jota-gateway"},
        headers=ha_bearer_headers,
    )
    assert r.status_code == 200, r.text
    assert captured["session_key"] == make_session_key("a", CLIENT_ID)


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
        json={**BODY, "model": "jota-gateway"},
        headers=ha_bearer_headers,
    )
    assert r.status_code == 403
    assert r.json()["reason"] == "agent_not_permitted"


def test_real_agent_as_model_still_works_and_unknown_still_403(
    client_untrusted, ha_bearer_headers, mock_orchestrator
):
    from src.core.session_key import make_session_key

    captured = _capture_session_key(mock_orchestrator)
    r = client_untrusted.post(
        "/v1/chat/completions", json={**BODY, "model": "a"}, headers=ha_bearer_headers
    )
    assert r.status_code == 200
    assert captured["session_key"] == make_session_key("a", CLIENT_ID)

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
