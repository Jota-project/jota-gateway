"""Descubrimiento y emparejamiento (#317): /.well-known/fogar-gateway, POST /pair y /admin/pairing."""

import json
import logging

import pytest
from sqlmodel import Session, select
from starlette.testclient import TestClient

from fogar_gateway.core.config import settings
from fogar_gateway.db.models import ClientRecord
from fogar_gateway.main import app
from fogar_gateway.services import pairing
from tests.integration.conftest import ADMIN_TOKEN, _configure_app_mocks

ADMIN = {"x-admin-token": ADMIN_TOKEN}
LAN_PEER = ("192.168.1.50", 40000)


@pytest.fixture
def make_client(mock_services, mock_registry, monkeypatch):
    _configure_app_mocks(mock_registry, monkeypatch)
    opened: list[TestClient] = []

    def make(peer=LAN_PEER, headers=None) -> TestClient:
        c = TestClient(app, client=peer, headers=headers or {})
        c.__enter__()
        opened.append(c)
        return c

    yield make
    for c in opened:
        c.__exit__(None, None, None)


@pytest.fixture
def http(make_client) -> TestClient:
    return make_client()


def _open(http: TestClient, **template) -> dict:
    r = http.post("/admin/pairing", json=template, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10**6:06d}"


def _rows(db_engine) -> list[ClientRecord]:
    with Session(db_engine) as s:
        return list(s.exec(select(ClientRecord)).all())


# --- /.well-known ---


def test_well_known_shape(http):
    r = http.get("/.well-known/fogar-gateway")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"service", "version", "name"}
    assert body["service"] == "fogar-gateway"
    assert body["name"]  # nombre del equipo por defecto


def test_well_known_uses_gateway_name(http, monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_NAME", "green-house")
    assert http.get("/.well-known/fogar-gateway").json()["name"] == "green-house"


# --- /admin/pairing ---


@pytest.mark.parametrize(
    "method,path",
    [("post", "/admin/pairing"), ("get", "/admin/pairing"), ("get", "/admin/pairing/x"),
     ("delete", "/admin/pairing/x")],
)
def test_admin_pairing_requires_token(http, method, path):
    kwargs = {"json": {}} if method == "post" else {}
    assert getattr(http, method)(path, headers={"x-admin-token": "wrong"}, **kwargs).status_code == 401


def test_admin_pairing_validates_template_upfront(http):
    r = http.post(
        "/admin/pairing", json={"default_agent": "a", "allowed_agents": ["b"]}, headers=ADMIN
    )
    assert r.status_code == 422
    assert http.post("/admin/pairing", json={"tts_speed": 0}, headers=ADMIN).status_code == 422
    assert http.post("/admin/pairing", json={"client_key": "x" * 40}, headers=ADMIN).status_code == 422


def test_admin_pairing_open_returns_code_once(http):
    item = _open(http, name="tele", client_type="screen")
    assert len(item["code"]) == 6 and item["code"].isdigit()
    assert item["template"] == {"name": "tele", "client_type": "screen"}
    listed = http.get("/admin/pairing", headers=ADMIN).json()
    one = http.get(f"/admin/pairing/{item['id']}", headers=ADMIN).json()
    assert [p["id"] for p in listed] == [item["id"]]
    assert "code" not in listed[0] and "code" not in one
    assert item["code"] not in json.dumps(listed) + json.dumps(one)


def test_admin_pairing_limit(http):
    for _ in range(pairing.MAX_OPEN):
        _open(http)
    assert http.post("/admin/pairing", json={}, headers=ADMIN).status_code == 409


def test_admin_pairing_cancel(http):
    item = _open(http)
    assert http.delete(f"/admin/pairing/{item['id']}", headers=ADMIN).status_code == 204
    assert http.get(f"/admin/pairing/{item['id']}", headers=ADMIN).json()["state"] == "cancelled"
    assert http.delete(f"/admin/pairing/{item['id']}", headers=ADMIN).status_code == 404
    r = http.post("/pair", json={"code": item["code"]})
    assert r.status_code == 400


# --- POST /pair ---


def test_pair_creates_client_with_template(http, db_engine):
    item = _open(
        http, name="tele", client_type="screen", default_agent="j-assistant",
        allowed_agents=["j-assistant"], tts_voice="ef_dora", barge_in_enabled=False,
    )
    r = http.post("/pair", json={"code": item["code"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"client_id", "name", "client_key", "gateway"}
    assert body["name"] == "tele"
    assert set(body["gateway"]) == {"name", "version"}
    [row] = _rows(db_engine)
    assert row.id == body["client_id"] and row.client_key == body["client_key"]
    assert (row.client_type, row.default_agent, row.tts_voice, row.barge_in_enabled) == (
        "screen", "j-assistant", "ef_dora", False,
    )
    assert json.loads(row.allowed_agents) == ["j-assistant"]


def test_pair_template_wins_over_client_fields(http, db_engine):
    item = _open(http, name="tele", client_type="screen", output_mode=["text"])
    r = http.post(
        "/pair",
        json={"code": item["code"], "name": "otro", "client_type": "app",
              "output_mode": ["audio", "text"]},
    )
    assert r.status_code == 201
    [row] = _rows(db_engine)
    assert (row.name, row.client_type, json.loads(row.output_mode)) == ("tele", "screen", ["text"])


def test_pair_client_fields_fill_gaps(http, db_engine):
    item = _open(http, default_agent="j-assistant")
    r = http.post(
        "/pair",
        json={"code": item["code"], "name": "Tele del salón", "client_type": "screen",
              "output_mode": ["audio", "text", "status"]},
    )
    assert r.status_code == 201
    [row] = _rows(db_engine)
    assert (row.name, row.client_type, row.default_agent) == ("Tele del salón", "screen", "j-assistant")
    assert json.loads(row.output_mode) == ["audio", "text", "status"]


def test_null_template_fields_do_not_override_client(http, db_engine):
    item = _open(http, name=None, client_type=None, output_mode=None, default_agent="j")
    assert item["template"] == {"default_agent": "j"}
    r = http.post("/pair", json={"code": item["code"], "name": "tele", "client_type": "screen"})
    assert r.status_code == 201, r.text
    [row] = _rows(db_engine)
    assert (row.name, row.client_type, row.default_agent) == ("tele", "screen", "j")


def test_pair_without_any_name_gets_one(http, db_engine):
    item = _open(http)
    assert http.post("/pair", json={"code": item["code"], "client_type": "screen"}).status_code == 201
    [row] = _rows(db_engine)
    assert row.name.startswith("screen-")


def test_pair_rejects_invalid_client_fields(http, db_engine):
    item = _open(http)
    r = http.post("/pair", json={"code": item["code"], "client_type": "tostadora"})
    assert r.status_code == 422
    assert _rows(db_engine) == []
    # Un 422 no cuenta como intento: el código sigue sirviendo.
    assert http.post("/pair", json={"code": item["code"]}).status_code == 201


def test_pair_invalid_code_generic_400(http, monkeypatch):
    used = _open(http)
    assert http.post("/pair", json={"code": used["code"]}).status_code == 201
    expired = _open(http)
    item = http.app.state.pairing.get(expired["id"])
    item.deadline = 0  # vencido
    _open(http)  # uno abierto, para que los fallos cuenten
    bodies = [
        http.post("/pair", json={"code": c})
        for c in (used["code"], expired["code"], "12ab", "")
    ]
    assert {r.status_code for r in bodies} == {400}
    assert {r.text for r in bodies} == {'{"detail":"invalid or expired code"}'}


def test_pair_rejected_without_open_codes(http):
    r = http.post("/pair", json={"code": "123456"})
    assert r.status_code == 400


def test_failed_attempts_burn_all_codes(http):
    a, b = _open(http), _open(http)
    for _ in range(pairing.MAX_FAILED):
        assert http.post("/pair", json={"code": _wrong(a["code"])}).status_code == 400
    assert http.get("/admin/pairing", headers=ADMIN).json() == []
    assert http.post("/pair", json={"code": b["code"]}).status_code == 400


def test_create_failure_keeps_code_open(http, monkeypatch, db_engine):
    from fastapi import HTTPException

    item = _open(http)
    calls = []

    def boom(session, body):
        calls.append(body)
        raise HTTPException(status_code=409, detail="client_key already exists")

    monkeypatch.setattr("fogar_gateway.api.pairing_routes.create_client_record", boom)
    for _ in range(pairing.MAX_FAILED):
        assert http.post("/pair", json={"code": item["code"]}).status_code == 409
    monkeypatch.undo()
    assert http.get(f"/admin/pairing/{item['id']}", headers=ADMIN).json()["state"] == "open"


def test_pairing_state_after_use(http):
    item = _open(http, name="tele")
    client_id = http.post("/pair", json={"code": item["code"]}).json()["client_id"]
    state = http.get(f"/admin/pairing/{item['id']}", headers=ADMIN).json()
    assert (state["state"], state["client_id"], state["client_name"]) == ("used", client_id, "tele")


def test_paired_key_authenticates(make_client):
    http = make_client(("203.0.113.5", 54321))  # fuera de la LAN de confianza: exige clave
    item = _open(http)
    key = http.post("/pair", json={"code": item["code"]}).json()["client_key"]
    assert http.get("/v1/models").status_code == 401
    assert http.get("/v1/models", headers={"authorization": f"Bearer {key}"}).status_code == 200


def test_pair_logs_never_contain_code_or_key(http, caplog):
    caplog.set_level(logging.DEBUG)
    item = _open(http)
    http.post("/pair", json={"code": _wrong(item["code"])})
    key = http.post("/pair", json={"code": item["code"]}).json()["client_key"]
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Emparejamiento: cliente" in text
    assert item["code"] not in text and key not in text
    assert _wrong(item["code"]) not in text
