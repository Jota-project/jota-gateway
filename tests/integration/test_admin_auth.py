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


def test_admin_token_compared_in_constant_time(client, admin_headers, monkeypatch):
    """G-30 (#216): la comparación pasa por secrets.compare_digest, no por `!=`."""
    import secrets

    calls = []
    real = secrets.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(secrets, "compare_digest", spy)
    assert client.get("/admin/sessions", headers=admin_headers).status_code == 200
    assert len(calls) == 1


def test_admin_non_ascii_token_returns_401_not_500(client):
    # compare_digest(str, str) raises TypeError on non-ASCII; we compare bytes.
    r = client.get("/admin/sessions", headers={"x-admin-token": "tökén".encode("latin-1")})
    assert r.status_code == 401
