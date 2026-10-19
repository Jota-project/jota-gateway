"""Consistencia de default_agent/allowed_agents y rotate-key sin 500 (#233)."""

import json
import logging

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

ADMIN_TOKEN = "test-admin-token"
HEADERS = {"x-admin-token": ADMIN_TOKEN}
TAKEN_KEY = "taken-secret-key-0123456789abcdef"


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


def _create(http, **body):
    return http.post("/admin/clients", json={"name": "c", **body}, headers=HEADERS)


# --- POST -----------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"default_agent": ""},
        {"default_agent": "   "},
        {"allowed_agents": [""]},
        {"allowed_agents": ["main", " "]},
    ],
)
def test_post_empty_agent_strings_are_422(http, body):
    assert _create(http, **body).status_code == 422


def test_post_default_outside_allowed_is_422(http):
    r = _create(http, default_agent="x", allowed_agents=["a", "b"])
    assert r.status_code == 422


def test_post_default_with_empty_allowed_is_422(http):
    assert _create(http, default_agent="x", allowed_agents=[]).status_code == 422


def test_post_valid_combinations_still_work(http):
    assert _create(http, default_agent="a", allowed_agents=["a", "b"]).status_code == 201
    assert _create(http, default_agent="z").status_code == 201  # allowed null = sin restricción
    assert _create(http, allowed_agents=[]).status_code == 201  # [] = denegado, sin default


# --- PATCH (estado resultante) ---------------------------------------------


def test_patch_default_checked_against_stored_allowed(http):
    cid = _create(http, allowed_agents=["a", "b"]).json()["id"]
    r = http.patch(f"/admin/clients/{cid}", json={"default_agent": "x"}, headers=HEADERS)
    assert r.status_code == 422
    r = http.patch(f"/admin/clients/{cid}", json={"default_agent": "a"}, headers=HEADERS)
    assert r.status_code == 200


def test_patch_allowed_checked_against_stored_default(http):
    cid = _create(http, default_agent="a").json()["id"]
    r = http.patch(f"/admin/clients/{cid}", json={"allowed_agents": ["b"]}, headers=HEADERS)
    assert r.status_code == 422
    assert http.get(f"/admin/clients/{cid}", headers=HEADERS).json()["allowed_agents"] is None
    r = http.patch(f"/admin/clients/{cid}", json={"allowed_agents": None}, headers=HEADERS)
    assert r.status_code == 200


def test_patch_both_fields_together_use_resulting_state(http):
    cid = _create(http, default_agent="a", allowed_agents=["a"]).json()["id"]
    r = http.patch(
        f"/admin/clients/{cid}",
        json={"default_agent": "b", "allowed_agents": ["b"]},
        headers=HEADERS,
    )
    assert r.status_code == 200


def test_patch_clearing_default_is_allowed(http):
    cid = _create(http, default_agent="a", allowed_agents=["a"]).json()["id"]
    r = http.patch(f"/admin/clients/{cid}", json={"default_agent": None}, headers=HEADERS)
    assert r.status_code == 200


def test_patch_empty_strings_are_422(http):
    cid = _create(http).json()["id"]
    for body in ({"default_agent": ""}, {"allowed_agents": [""]}):
        assert http.patch(f"/admin/clients/{cid}", json=body, headers=HEADERS).status_code == 422


# --- Filas antiguas inconsistentes (#122: solo se valida la entrada) -------


def _legacy_inconsistent(db_engine):
    from src.db.models import ClientRecord

    with Session(db_engine) as s:
        rec = ClientRecord(
            name="legacy",
            client_key="legacy-key-0123456789",
            default_agent="x",
            allowed_agents=json.dumps(["a"]),
        )
        s.add(rec)
        s.commit()
        return rec.id


def test_legacy_inconsistent_row_still_reads_and_unrelated_patch_works(http, db_engine):
    cid = _legacy_inconsistent(db_engine)
    r = http.get(f"/admin/clients/{cid}", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["default_agent"] == "x"
    r = http.patch(f"/admin/clients/{cid}", json={"name": "renamed"}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["name"] == "renamed"


def test_legacy_inconsistent_row_can_be_fixed_through_patch(http, db_engine):
    cid = _legacy_inconsistent(db_engine)
    r = http.patch(f"/admin/clients/{cid}", json={"default_agent": "a"}, headers=HEADERS)
    assert r.status_code == 200


# --- rotate-key ------------------------------------------------------------


def _fake_tokens(monkeypatch, values):
    from src.api import admin_routes

    it = iter(values)
    last = values[-1]
    monkeypatch.setattr(admin_routes.secrets, "token_urlsafe", lambda n=None: next(it, last))


def test_rotate_key_collision_retries_with_new_key(http, monkeypatch):
    http.post("/admin/clients", json={"name": "A", "client_key": TAKEN_KEY}, headers=HEADERS)
    cid = _create(http).json()["id"]
    _fake_tokens(monkeypatch, [TAKEN_KEY, "fresh-key-abcdefghijklmnop"])
    r = http.post(f"/admin/clients/{cid}/rotate-key", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["client_key"] == "fresh-key-abcdefghijklmnop"


def test_rotate_key_persistent_collision_is_409_without_leaking_key(http, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    http.post("/admin/clients", json={"name": "A", "client_key": TAKEN_KEY}, headers=HEADERS)
    created = _create(http).json()
    _fake_tokens(monkeypatch, [TAKEN_KEY])
    r = http.post(f"/admin/clients/{created['id']}/rotate-key", headers=HEADERS)
    assert r.status_code == 409
    assert TAKEN_KEY not in r.text
    assert TAKEN_KEY not in caplog.text
    # La fila conserva su clave anterior
    again = http.get(f"/admin/clients/{created['id']}", headers=HEADERS).json()
    assert again["client_key"] == created["client_key"]
