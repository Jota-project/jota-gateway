"""Validación de entrada y duplicados en /admin/clients (#217, G-28 / G-27a)."""

import logging
import traceback

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

ADMIN_TOKEN = "test-admin-token"
HEADERS = {"x-admin-token": ADMIN_TOKEN}
JSON_HEADERS = {**HEADERS, "content-type": "application/json"}
SECRET_KEY = "dup-secret-key-0123456789abcdef"


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


def test_create_duplicate_client_key_returns_409_without_leaking_key(http, caplog):
    caplog.set_level(logging.DEBUG)
    r1 = http.post("/admin/clients", json={"name": "A", "client_key": SECRET_KEY}, headers=HEADERS)
    assert r1.status_code == 201
    r2 = http.post("/admin/clients", json={"name": "B", "client_key": SECRET_KEY}, headers=HEADERS)
    assert r2.status_code == 409
    assert SECRET_KEY not in r2.text
    assert SECRET_KEY not in caplog.text
    for rec in caplog.records:
        assert SECRET_KEY not in rec.getMessage()
        if rec.exc_info:
            assert SECRET_KEY not in "".join(traceback.format_exception(*rec.exc_info))


def test_create_empty_client_key_still_generates_one(http):
    r = http.post("/admin/clients", json={"name": "A", "client_key": ""}, headers=HEADERS)
    assert r.status_code == 201
    assert len(r.json()["client_key"]) > 20


def test_create_short_client_key_is_accepted(http):
    r = http.post("/admin/clients", json={"name": "A", "client_key": "abc"}, headers=HEADERS)
    assert r.status_code == 201
    assert r.json()["client_key"] == "abc"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": "x", "stt_language": None},
        {"name": "x", "tts_speed": 0},
        {"name": "x", "tts_speed": -1.0},
        {"name": "x", "silence_timeout_s": 0},
        {"name": "x", "silence_timeout_s": -2},
        {"name": "x", "max_silence_turns": 0},
        {"name": "x", "max_silence_turns": -1},
        {"name": "x", "barge_in_min_chars": -1},
        {"name": "x", "stt_vad_thold": -0.5},
        {"name": "x", "max_silence_turns": 10**30},
    ],
)
def test_create_invalid_values_return_422(http, payload):
    r = http.post("/admin/clients", json=payload, headers=HEADERS)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_create_nan_inf_return_422(http, literal):
    r = http.post(
        "/admin/clients",
        content='{"name": "x", "tts_speed": %s}' % literal,
        headers=JSON_HEADERS,
    )
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "payload",
    [
        {"name": None},
        {"name": ""},
        {"is_active": None},
        {"stt_language": None},
        {"stt_vad_thold": None},
        {"tts_voice": None},
        {"tts_speed": None},
        {"tts_speed": 0},
        {"barge_in_enabled": None},
        {"barge_in_min_chars": None},
        {"silence_timeout_s": None},
        {"silence_timeout_s": -1},
        {"max_silence_turns": None},
        {"max_silence_turns": 0},
        {"push_enabled": None},
        {"tool_calls_enabled": None},
    ],
)
def test_patch_invalid_or_null_values_return_422(http, payload):
    cid = http.post("/admin/clients", json={"name": "A"}, headers=HEADERS).json()["id"]
    r = http.patch(f"/admin/clients/{cid}", json=payload, headers=HEADERS)
    assert r.status_code == 422, r.text
    assert http.get(f"/admin/clients/{cid}", headers=HEADERS).json()["name"] == "A"


def test_patch_nan_returns_422(http):
    cid = http.post("/admin/clients", json={"name": "A"}, headers=HEADERS).json()["id"]
    r = http.patch(f"/admin/clients/{cid}", content='{"tts_speed": NaN}', headers=JSON_HEADERS)
    assert r.status_code == 422, r.text


def test_patch_nullable_fields_can_still_be_cleared(http):
    cid = http.post(
        "/admin/clients",
        json={"name": "A", "default_agent": "x", "client_type": "ha"},
        headers=HEADERS,
    ).json()["id"]
    r = http.patch(
        f"/admin/clients/{cid}",
        json={"default_agent": None, "client_type": None, "allowed_agents": None},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["default_agent"] is None
    assert r.json()["client_type"] is None
