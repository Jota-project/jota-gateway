"""Tests para validación de admin token (deps.py → get_admin_auth)."""


def test_missing_admin_token_returns_422(client):
    """Sin X-Admin-Token header → FastAPI devuelve 422 (header requerido ausente)."""
    r = client.get("/admin/orchestrators/openclaw/status")
    assert r.status_code == 422


def test_invalid_admin_token_returns_401(client):
    """Token inválido → 401."""
    r = client.get("/admin/orchestrators/openclaw/status", headers={"x-admin-token": "wrong-token"})
    assert r.status_code == 401


def test_valid_admin_token_passes(client, admin_headers):
    """Token válido → request pasa."""
    r = client.get("/admin/orchestrators/openclaw/status", headers=admin_headers)
    assert r.status_code == 200
