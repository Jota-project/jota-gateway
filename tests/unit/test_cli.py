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


def test_add_client_rejects_unknown_type():
    import pytest

    e = _engine()
    with pytest.raises(SystemExit) as exc:
        _run(["add-client", "--name", "X", "--type", "toaster"], e)
    assert exc.value.code == 2
    with Session(e) as s:
        assert s.exec(select(ClientRecord)).all() == []


def test_list_clients_never_prints_key_characters():
    """G-27c (#216): list-clients muestra la huella (fp), no caracteres de la key."""
    from src.core.logging import fingerprint_key

    e = _engine()
    key = "Zq9xKLMNOPQRSTUVWXYZabcdefghij0123456789"
    _run(["add-client", "--name", "Dev", "--key", key], e)
    out = _run(["list-clients"], e)
    assert key[:6] not in out
    assert fingerprint_key(key) in out


def test_unknown_key_error_does_not_echo_the_key(capsys):
    """G-27c (#216): `_require` no imprime la key (ni entera ni en parte) en stderr."""
    import pytest

    from src.core.logging import fingerprint_key

    e = _engine()
    key = "Zq9xKLMNOPQRSTUVWXYZabcdefghij0123456789"
    with pytest.raises(SystemExit) as exc:
        _run(["delete-client", key], e)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert key[:6] not in err
    assert fingerprint_key(key) in err


def test_add_client_strips_agent_and_rejects_empty_agent(capsys):
    """#239: --agent se recorta como en el admin REST; vacío tras recortar -> error, sin fila."""
    import pytest

    e = _engine()
    _run(["add-client", "--name", "HA", "--agent", "  home  "], e)
    with Session(e) as s:
        assert s.exec(select(ClientRecord)).one().default_agent == "home"

    e = _engine()
    with pytest.raises(SystemExit) as exc:
        _run(["add-client", "--name", "X", "--agent", "   "], e)
    assert exc.value.code != 0
    assert "--agent" in capsys.readouterr().err
    with Session(e) as s:
        assert s.exec(select(ClientRecord)).all() == []
