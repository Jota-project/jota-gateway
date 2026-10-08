from io import StringIO
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.db.models import ClientRecord


def _engine():
    e = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


def _run(args: list[str], engine) -> str:
    from src.cli import run

    out = StringIO()
    with patch("src.cli._get_engine", return_value=engine), patch("sys.stdout", out):
        run(args)
    return out.getvalue()


def test_add_client_creates_record_and_prints_key():
    e = _engine()
    output = _run(["add-client", "--name", "ESP32 salón"], e)
    assert "client_key" in output
    with Session(e) as s:
        recs = s.exec(select(ClientRecord)).all()
    assert len(recs) == 1
    assert recs[0].name == "ESP32 salón"
    assert recs[0].is_active is True


def test_add_client_with_type_and_agent():
    e = _engine()
    _run(["add-client", "--name", "HA", "--type", "ha", "--agent", "home"], e)
    with Session(e) as s:
        rec = s.exec(select(ClientRecord)).first()
    assert rec.client_type == "ha"
    assert rec.default_agent == "home"


def test_list_clients_shows_name():
    e = _engine()
    _run(["add-client", "--name", "TestClient"], e)
    out = _run(["list-clients"], e)
    assert "TestClient" in out


def test_deactivate_and_activate():
    e = _engine()
    _run(["add-client", "--name", "Dev"], e)
    with Session(e) as s:
        key = s.exec(select(ClientRecord)).first().client_key
    _run(["deactivate-client", key], e)
    with Session(e) as s:
        assert s.exec(select(ClientRecord)).first().is_active is False
    _run(["activate-client", key], e)
    with Session(e) as s:
        assert s.exec(select(ClientRecord)).first().is_active is True


def test_delete_client():
    e = _engine()
    _run(["add-client", "--name", "ToDelete"], e)
    with Session(e) as s:
        key = s.exec(select(ClientRecord)).first().client_key
    _run(["delete-client", key], e)
    with Session(e) as s:
        assert s.exec(select(ClientRecord)).first() is None


def test_delete_client_accepts_key_starting_with_dash():
    """secrets.token_urlsafe(32) (used for generated client_keys) can start
    with '-' — argparse then misreads the positional client_key as an
    unknown option ('the following arguments are required: client_key'), a
    real bug for any user whose generated key happens to start with '-', not
    just bad luck in this test. Insert the record directly to isolate the
    positional-argument parsing bug from add-client's own --key parsing."""
    e = _engine()
    with Session(e) as s:
        s.add(ClientRecord(name="ToDelete", client_key="-AbCdEf123"))
        s.commit()
    _run(["delete-client", "-AbCdEf123"], e)
    with Session(e) as s:
        assert s.exec(select(ClientRecord)).first() is None


def _generation(key: str) -> int:
    from src.services.db_client import db_client

    return db_client._generations.get(key, 0)


def test_mutating_commands_invalidate_db_client_cache():
    e = _engine()
    _run(["add-client", "--name", "Dev", "--key", "cli-inval-key"], e)
    after_add = _generation("cli-inval-key")
    assert after_add >= 1, "add-client no invalidó la caché"

    _run(["deactivate-client", "cli-inval-key"], e)
    after_deactivate = _generation("cli-inval-key")
    assert after_deactivate > after_add

    _run(["activate-client", "cli-inval-key"], e)
    after_activate = _generation("cli-inval-key")
    assert after_activate > after_deactivate

    _run(["delete-client", "cli-inval-key"], e)
    assert _generation("cli-inval-key") > after_activate


def test_add_client_rejects_unknown_type():
    import pytest

    e = _engine()
    with pytest.raises(SystemExit) as exc:
        _run(["add-client", "--name", "X", "--type", "toaster"], e)
    assert exc.value.code == 2
    with Session(e) as s:
        assert s.exec(select(ClientRecord)).all() == []
