"""Tests para /admin/clients/* CRUD."""
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine


ADMIN_TOKEN = "test-admin-token"
HEADERS = {"x-admin-token": ADMIN_TOKEN}


@pytest.fixture(autouse=True)
def override_admin_token(monkeypatch):
    from src.core import config
    monkeypatch.setattr(config.settings, "ADMIN_TOKEN", ADMIN_TOKEN)


@pytest.fixture(autouse=True)
def db_engine(monkeypatch):
    from src import db
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db.database, "_engine", engine)
    return engine


@pytest.fixture()
def http(db_engine):
    from starlette.testclient import TestClient
    from src.main import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# --- CREATE ---

def test_create_client_minimal(http):
    r = http.post("/admin/clients", json={"name": "ESP32"}, headers=HEADERS)
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "ESP32"
    assert len(data["client_key"]) > 20
    assert data["is_active"] is True
    assert data["stt_language"] == "es"


def test_create_client_full(http):
    body = {
        "name": "HA Bridge",
        "client_type": "ha",
        "default_agent": "home",
        "allowed_agents": ["home", "kitchen"],
        "tts_voice": "en_heart",
        "stt_language": "en",
        "barge_in_min_chars": 10,
    }
    r = http.post("/admin/clients", json=body, headers=HEADERS)
    assert r.status_code == 201
    data = r.json()
    assert data["client_type"] == "ha"
    assert data["default_agent"] == "home"
    assert data["allowed_agents"] == ["home", "kitchen"]
    assert data["tts_voice"] == "en_heart"


# --- LIST ---

def test_list_clients_empty(http):
    r = http.get("/admin/clients", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == []


def test_list_clients_after_creates(http):
    http.post("/admin/clients", json={"name": "A"}, headers=HEADERS)
    http.post("/admin/clients", json={"name": "B"}, headers=HEADERS)
    r = http.get("/admin/clients", headers=HEADERS)
    assert r.status_code == 200
    assert len(r.json()) == 2


# --- GET ---

def test_get_client(http):
    created = http.post("/admin/clients", json={"name": "X"}, headers=HEADERS).json()
    r = http.get(f"/admin/clients/{created['id']}", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_get_client_not_found(http):
    r = http.get("/admin/clients/nonexistent", headers=HEADERS)
    assert r.status_code == 404


# --- PATCH ---

def test_patch_client_name(http):
    created = http.post("/admin/clients", json={"name": "Old"}, headers=HEADERS).json()
    r = http.patch(f"/admin/clients/{created['id']}", json={"name": "New"}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["name"] == "New"


def test_patch_client_config(http):
    created = http.post("/admin/clients", json={"name": "C"}, headers=HEADERS).json()
    r = http.patch(
        f"/admin/clients/{created['id']}",
        json={"tts_voice": "en_heart", "barge_in_enabled": False},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["tts_voice"] == "en_heart"
    assert r.json()["barge_in_enabled"] is False


def test_patch_deactivate(http):
    created = http.post("/admin/clients", json={"name": "D"}, headers=HEADERS).json()
    r = http.patch(f"/admin/clients/{created['id']}", json={"is_active": False}, headers=HEADERS)
    assert r.json()["is_active"] is False


# --- DELETE ---

def test_delete_client(http):
    created = http.post("/admin/clients", json={"name": "Del"}, headers=HEADERS).json()
    r = http.delete(f"/admin/clients/{created['id']}", headers=HEADERS)
    assert r.status_code == 204
    r2 = http.get(f"/admin/clients/{created['id']}", headers=HEADERS)
    assert r2.status_code == 404


# --- ROTATE KEY ---

def test_rotate_key(http):
    created = http.post("/admin/clients", json={"name": "R"}, headers=HEADERS).json()
    old_key = created["client_key"]
    r = http.post(f"/admin/clients/{created['id']}/rotate-key", headers=HEADERS)
    assert r.status_code == 200
    new_key = r.json()["client_key"]
    assert new_key != old_key
    assert len(new_key) > 20


# --- AUTH ---

def test_missing_token_returns_422(http):
    r = http.get("/admin/clients")
    assert r.status_code == 422


def test_wrong_token_returns_401(http):
    r = http.get("/admin/clients", headers={"x-admin-token": "bad"})
    assert r.status_code == 401


# --- tool_calls_enabled ---

def test_create_client_tool_calls_enabled_defaults_false(http):
    r = http.post("/admin/clients", json={"name": "T"}, headers=HEADERS)
    assert r.status_code == 201
    assert r.json()["tool_calls_enabled"] is False


def test_create_client_tool_calls_enabled_explicit_true(http):
    r = http.post("/admin/clients", json={"name": "T", "tool_calls_enabled": True}, headers=HEADERS)
    assert r.status_code == 201
    assert r.json()["tool_calls_enabled"] is True


def test_patch_client_tool_calls_enabled(http):
    created = http.post("/admin/clients", json={"name": "C"}, headers=HEADERS).json()
    r = http.patch(
        f"/admin/clients/{created['id']}",
        json={"tool_calls_enabled": True},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["tool_calls_enabled"] is True
