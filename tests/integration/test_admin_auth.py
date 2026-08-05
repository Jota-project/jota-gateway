"""Tests for /admin/* authentication."""


def test_admin_missing_token_returns_422(client):
    """No X-Admin-Token header → 422 (missing required header)."""
    r = client.get("/admin/sessions")
    assert r.status_code == 422


def test_admin_wrong_token_returns_401(client):
    r = client.get("/admin/sessions", headers={"x-admin-token": "wrong"})
    assert r.status_code == 401


def test_admin_no_token_configured_returns_503(client, monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "ADMIN_TOKEN", "")
    r = client.get("/admin/sessions", headers={"x-admin-token": "anything"})
    assert r.status_code == 503
