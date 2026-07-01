"""
cli.py
~~~~~~
CLI de gestión de clientes jota-gateway.
Uso: python src/cli.py <command> [options]
"""
import secrets
import sys
from typing import Optional

from sqlmodel import Session, select

from src.db.database import create_db_and_tables, get_engine
from src.db.models import ClientRecord


def _get_engine():
    """Indirection para monkeypatching en tests."""
    return get_engine()


def _require(session: Session, client_key: str) -> ClientRecord:
    rec = session.exec(select(ClientRecord).where(ClientRecord.client_key == client_key)).first()
    if rec is None:
        print(f"ERROR: No se encontró cliente con key '{client_key}'", file=sys.stderr)
        sys.exit(1)
    return rec


def cmd_add(name: str, client_type: Optional[str], agent: Optional[str], engine,
            client_key: Optional[str] = None) -> None:
    key = client_key or secrets.token_urlsafe(32)
    with Session(engine) as s:
        rec = ClientRecord(name=name, client_key=key, client_type=client_type, default_agent=agent)
        s.add(rec)
        s.commit()
        s.refresh(rec)
    print("Cliente creado:")
    print(f"  name:       {rec.name}")
    print(f"  id:         {rec.id}")
    print(f"  client_key: {rec.client_key}")


def cmd_list(engine) -> None:
    with Session(engine) as s:
        records = s.exec(select(ClientRecord)).all()
    if not records:
        print("(sin clientes)")
        return
    fmt = "{:<36}  {:<20}  {:<8}  {}"
    print(fmt.format("ID", "NAME", "ACTIVE", "KEY (primeros 20 chars)"))
    print("-" * 90)
    for r in records:
        print(fmt.format(r.id, r.name[:20], str(r.is_active), r.client_key[:20] + "..."))


def cmd_deactivate(client_key: str, engine) -> None:
    with Session(engine) as s:
        rec = _require(s, client_key)
        rec.is_active = False
        s.add(rec)
        s.commit()
    print("Cliente desactivado.")


def cmd_activate(client_key: str, engine) -> None:
    with Session(engine) as s:
        rec = _require(s, client_key)
        rec.is_active = True
        s.add(rec)
        s.commit()
    print("Cliente activado.")


def cmd_delete(client_key: str, engine) -> None:
    with Session(engine) as s:
        rec = _require(s, client_key)
        s.delete(rec)
        s.commit()
    print("Cliente eliminado.")


def run(argv: list[str]) -> None:
    import argparse

    p = argparse.ArgumentParser(prog="python src/cli.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add-client")
    a.add_argument("--name", required=True)
    a.add_argument("--key", dest="client_key", help="client_key exacto a usar (si se omite, se genera uno)")
    a.add_argument("--type", dest="client_type")
    a.add_argument("--agent")

    sub.add_parser("list-clients")

    for name in ("deactivate-client", "activate-client", "delete-client"):
        sp = sub.add_parser(name)
        sp.add_argument("client_key")

    args = p.parse_args(argv)
    engine = _get_engine()

    if args.cmd == "add-client":
        cmd_add(args.name, args.client_type, args.agent, engine, args.client_key)
    elif args.cmd == "list-clients":
        cmd_list(engine)
    elif args.cmd == "deactivate-client":
        cmd_deactivate(args.client_key, engine)
    elif args.cmd == "activate-client":
        cmd_activate(args.client_key, engine)
    elif args.cmd == "delete-client":
        cmd_delete(args.client_key, engine)


if __name__ == "__main__":
    create_db_and_tables()
    run(sys.argv[1:])
