"""#234: /v1/models lista el roster; #228: errores estables en /v1."""

import json
import logging

from sqlmodel import Session

from src.db.models import ClientRecord
from src.services.protocol import OrchestratorEvent
from tests.integration.conftest import CLIENT_ID
from tests.integration.test_rest_openai import _parse_sse_payloads

MARK = "SECRET-INTERNAL"
BODY = {"messages": [{"role": "user", "content": "Hola"}], "stream": False}


def _ids(r):
    return [m["id"] for m in r.json()["data"]]


# ── #234 ──────────────────────────────────────────────────────────────────────


def test_models_lists_alias_and_roster_agents_for_bearer_caller(
    client_untrusted, ha_bearer_headers
):
    r = client_untrusted.get("/v1/models", headers=ha_bearer_headers)
    assert r.status_code == 200
    assert _ids(r) == ["jota-gateway", "main", "a", "openclaw"]
    assert all(m["object"] == "model" for m in r.json()["data"])


def test_models_for_legacy_trusted_caller_lists_only_alias(client):
    """Origen confiable SIN Bearer: chat ignora body.model y usa el agente por defecto, así
    que no se anuncian agentes que no se podrían elegir."""
    assert _ids(client.get("/v1/models")) == ["jota-gateway"]


def _chat_session_key(c, mock_orchestrator, model, headers=None):
    captured = {}

    async def _stream(text, user_id, model_id=None, session_key=None):
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
        expected = "main" if mid == "jota-gateway" else mid
        assert sk.split(":")[1] == expected


def test_models_chat_coherence_legacy_listed_model_is_what_chat_uses(
    client, mock_orchestrator
):
    listed = _ids(client.get("/v1/models"))
    assert listed == ["jota-gateway"]
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
    assert _ids(r) == ["jota-gateway", "a"]


def test_models_with_empty_allowed_agents_lists_only_alias(
    client_untrusted, ha_bearer_headers, db_engine
):
    with Session(db_engine) as s:
        rec = s.get(ClientRecord, CLIENT_ID)
        rec.allowed_agents = "[]"
        s.add(rec)
        s.commit()
    r = client_untrusted.get("/v1/models", headers=ha_bearer_headers)
    assert _ids(r) == ["jota-gateway"]


def test_models_without_allowed_agents_restriction_lists_full_roster(
    client_untrusted, ha_bearer_headers
):
    r = client_untrusted.get("/v1/models", headers=ha_bearer_headers)
    assert _ids(r) == ["jota-gateway", "main", "a", "openclaw"]


def test_models_without_roster_lists_only_alias(client, mock_orchestrator):
    mock_orchestrator.gateway_info = None
    assert _ids(client.get("/v1/models")) == ["jota-gateway"]


def test_models_with_empty_roster_lists_only_alias(client, mock_orchestrator):
    mock_orchestrator.gateway_info.agents = {}
    assert _ids(client.get("/v1/models")) == ["jota-gateway"]


def test_models_never_duplicates_the_alias(client_untrusted, ha_bearer_headers, mock_orchestrator):
    from src.services.openclaw.models import AgentInfo

    mock_orchestrator.gateway_info.agents["jota-gateway"] = AgentInfo("jota-gateway", "x", False)
    ids = _ids(client_untrusted.get("/v1/models", headers=ha_bearer_headers))
    assert ids.count("jota-gateway") == 1 and len(ids) > 1


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
    caplog.set_level(logging.DEBUG, logger="src")

    async def _stream(*a, **k):
        raise RuntimeError(MARK)
        yield  # pragma: no cover

    mock_orchestrator.stream_response = _stream
    r = client.post("/v1/chat/completions", json=BODY)
    assert r.status_code == 502
    assert MARK not in r.text
    assert r.json() == {"error": "internal_error"}
    assert MARK not in "\n".join(
        x.getMessage() for x in caplog.records if x.name.startswith("src")
    )


def test_foreign_runtimeerror_text_never_reaches_v1_stream(client, mock_orchestrator, caplog):
    caplog.set_level(logging.DEBUG, logger="src")

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
        x.getMessage() for x in caplog.records if x.name.startswith("src")
    )
