"""/admin/state y /admin/state/stream (#219): instantánea viva sin texto ni claves."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.integration.conftest import CLIENT_ID, CLIENT_NAME, VALID_KEY

ROUTES = ["/admin/state", "/admin/state/stream"]

SNAPSHOT_KEYS = {
    "session_id",
    "client_id",
    "client_name",
    "input_mode",
    "output_mode",
    "started_at",
    "age_s",
    "turn",
    "turn_count",
    "sender",
    "turn_queue",
    "push_backlog",
    "last_activity_s",
    "stt_state",
    "last_latencies",
}


@pytest.mark.parametrize("path", ROUTES)
def test_state_requires_admin_token(client, monkeypatch, path):
    from src.core.config import settings

    assert client.get(path).status_code == 422
    assert client.get(path, headers={"x-admin-token": "wrong"}).status_code == 401
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "")
    assert client.get(path, headers={"x-admin-token": "anything"}).status_code == 503


def test_state_shape_with_active_session(client, admin_headers):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"client_key": VALID_KEY, "input_mode": "text", "output_mode": ["text"]})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "send", "text": "MARCADOR-6g"})
        for _ in range(20):
            if ws.receive_json().get("type") == "turn_end":
                break
        else:
            pytest.fail("sin turn_end")

        r = client.get("/admin/state", headers=admin_headers)

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"gateway", "services", "totals", "sessions"}
    assert set(body["gateway"]) == {"started_at", "uptime_s"}
    assert body["services"]["orchestrator"]["state"] == "CONNECTED"
    assert {"provider", "state", "last_error"} <= set(body["services"]["stt"])
    assert {"provider", "state", "last_error"} <= set(body["services"]["tts"])
    assert body["totals"]["sessions_active"] == 1
    assert body["totals"]["sessions_opened"] >= 1

    [row] = [s for s in body["sessions"] if s["client_id"] == CLIENT_ID]
    assert set(row) == SNAPSHOT_KEYS
    assert row["turn_count"] >= 1
    assert row["client_name"] == CLIENT_NAME
    assert row["turn"]["kind"] in ("idle", "normal")
    assert row["input_mode"] == "text"
    assert row["stt_state"] is None
    assert set(row["sender"]) == {"pending_msgs", "pending_bytes", "peak_msgs", "peak_bytes"}
    assert set(row["turn_queue"]) == {"depth", "peak"}
    assert set(row["push_backlog"]) == {"pending", "peak"}
    assert row["started_at"] is not None

    assert "MARCADOR-6g" not in r.text
    assert VALID_KEY not in r.text


async def test_state_stream_emits_and_stops_on_disconnect(client, monkeypatch):
    import src.api.admin_routes as admin_routes
    from src.main import app

    monkeypatch.setattr(admin_routes, "STATE_STREAM_INTERVAL_S", 0.01)
    request = SimpleNamespace(app=app, is_disconnected=AsyncMock(side_effect=[False, False, True]))

    resp = await admin_routes.get_state_stream(request)
    assert resp.media_type == "text/event-stream"
    chunks = [c async for c in resp.body_iterator]

    assert len(chunks) == 2
    for c in chunks:
        text = c if isinstance(c, str) else c.decode()
        assert text.startswith("data: ") and text.endswith("\n\n")
        assert "sessions" in json.loads(text[len("data: ") :])

    await asyncio.sleep(0.05)
    leftovers = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task()
        and ("state_stream" in t.get_name() or "state_stream" in repr(t.get_coro()))
    ]
    assert leftovers == []
