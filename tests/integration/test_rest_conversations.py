"""Tests para endpoints de conversaciones."""
import httpx


def test_get_conversations_returns_list(client, auth_headers):
    r = client.get("/api/conversations", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert body[0]["id"] == "conv-1"


def test_get_messages_returns_list(client, auth_headers):
    r = client.get("/api/conversations/conv-1/messages", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_delete_conversation_returns_204(client, auth_headers):
    r = client.delete("/api/conversations/conv-1", headers=auth_headers)
    assert r.status_code == 204


def test_get_messages_not_found_propagates_404(client, auth_headers, mock_services):
    """404 de jota-db se propaga como 404 al cliente."""
    mock_services.get(
        url__regex=r"http://localhost:8001/conversations/.+/messages"
    ).mock(return_value=httpx.Response(404, json={"detail": "Not found"}))
    r = client.get("/api/conversations/nonexistent/messages", headers=auth_headers)
    assert r.status_code == 404
