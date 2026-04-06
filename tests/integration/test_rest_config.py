"""Tests para GET/PUT/POST /api/config."""
import httpx


def test_get_config_returns_client_config(client, auth_headers):
    """GET /api/config devuelve la config del cliente desde jota-db."""
    r = client.get("/api/config", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["stt_language"] == "es"
    assert body["tts_voice"] == "af_heart"
    assert "barge_in_enabled" in body


def test_put_config_returns_updated_config(client, auth_headers, mock_services):
    """PUT /api/config llama a jota-db y devuelve la config actualizada."""
    mock_services.put("http://localhost:8001/config/me").mock(
        return_value=httpx.Response(200, json={
            "stt_language": "en", "stt_vad_thold": 0.0,
            "tts_voice": "af_heart", "tts_speed": 1.0,
            "preferred_model_id": None, "system_prompt_extra": None,
            "barge_in_enabled": True, "barge_in_min_chars": 5,
            "conversation_memory_limit": 20,
        })
    )
    r = client.put("/api/config", json={"stt_language": "en"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["stt_language"] == "en"


def test_post_config_reset_returns_defaults(client, auth_headers):
    """POST /api/config/reset llama al reset de jota-db y devuelve defaults."""
    r = client.post("/api/config/reset", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["stt_language"] == "es"


def test_put_config_invalid_body_returns_422(client, auth_headers):
    """Body no-JSON en PUT → 422."""
    r = client.put(
        "/api/config",
        content=b"not-json",
        headers={**auth_headers, "content-type": "application/json"},
    )
    assert r.status_code == 422


def test_config_endpoint_without_auth_returns_422(client):
    """Sin X-API-Key → 422."""
    r = client.get("/api/config")
    assert r.status_code == 422
