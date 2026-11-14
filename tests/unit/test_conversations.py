"""Conversaciones (M19): reglas del día, la tabla, la importación de antes de M19, el índice
y el reparto del push (`ClientRegistry`). services/conversations.py."""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from fogar_gateway.core.config import settings
from fogar_gateway.db.models import ClientRecord, ConversationRecord
from fogar_gateway.services import conversations as conv_mod
from fogar_gateway.services.conversations import (
    Conversation,
    ConversationNotFound,
    ConversationService,
    LegacyImport,
    SessionConversation,
    conversation_day,
    history_messages,
    session_key_for,
    session_title,
)
from fogar_gateway.services.openclaw.registry import ClientRegistry


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'gateway.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        for cid in ("tv", "web"):
            s.add(ClientRecord(id=cid, name=cid.upper(), client_key=f"key-{cid}"))
        s.commit()
    return engine


@pytest.fixture
def service(engine):
    return ConversationService(engine)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock(datetime(2026, 11, 14, 12, 0))
    monkeypatch.setattr(conv_mod, "_now", c)
    return c


# ── El día ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "now, day",
    [
        (datetime(2026, 11, 14, 4, 59), date(2026, 11, 13)),  # antes del corte: aún es ayer
        (datetime(2026, 11, 14, 5, 0), date(2026, 11, 14)),
        (datetime(2026, 11, 14, 23, 59), date(2026, 11, 14)),
        (datetime(2026, 11, 15, 0, 30), date(2026, 11, 14)),
    ],
)
def test_the_day_starts_at_the_configured_hour(now, day):
    assert settings.CONVERSATION_DAY_START_HOUR == 5
    assert conversation_day(now) == day


def test_day_start_hour_zero_is_midnight(monkeypatch):
    monkeypatch.setattr(settings, "CONVERSATION_DAY_START_HOUR", 0)
    assert conversation_day(datetime(2026, 11, 14, 0, 1)) == date(2026, 11, 14)


def test_day_start_hour_is_validated():
    from pydantic import ValidationError

    from fogar_gateway.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(CONVERSATION_DAY_START_HOUR=24)  # type: ignore[call-arg]


def test_session_key_of_a_conversation():
    assert session_key_for("main", "abc") == "agent:main:fogar-abc"


# ── La tabla ─────────────────────────────────────────────────────────────


async def test_default_conversation_is_one_per_client_agent_and_day(service, clock):
    first = await service.default_for("tv", "main")
    assert first.kind == "default"
    assert first.day == date(2026, 11, 14)
    assert first.session_key == f"agent:main:fogar-{first.id}"
    assert await service.default_for("tv", "main") == first
    assert (await service.default_for("tv", "other")).id != first.id  # otro agente
    assert (await service.default_for("web", "main")).id != first.id  # otro cliente
    clock.now = datetime(2026, 11, 15, 5, 0)  # tras el corte del día siguiente
    tomorrow = await service.default_for("tv", "main")
    assert tomorrow.id != first.id and tomorrow.day == date(2026, 11, 15)


async def test_a_race_on_the_default_conversation_returns_the_same_row(service, engine):
    """El índice único (cliente, agente, día) resuelve dos altas a la vez."""
    day = date(2026, 11, 14)
    first = service._default_for("tv", "main", day)
    real = ConversationService._find_default(Session(engine), "tv", "main", day)
    # Como si la otra sesión no la hubiera visto al buscar: el alta choca y devuelve la suya.
    service._find_default = MagicMock(side_effect=[None, real])
    assert service._default_for("tv", "main", day).id == first.id


async def test_created_conversations_are_always_new(service):
    a, b = await service.create("web", "main"), await service.create("web", "main")
    assert a.id != b.id and a.kind == b.kind == "created" and a.day is None


async def test_a_conversation_of_another_client_is_not_found(service):
    web = await service.create("web", "main")
    assert (await service.get_owned("web", web.id)) == web
    with pytest.raises(ConversationNotFound):
        await service.get_owned("tv", web.id)
    with pytest.raises(ConversationNotFound):
        await service.get_owned("tv", "does-not-exist")


async def test_note_turn_counts_and_stamps(service, engine):
    conv = await service.create("web", "main")
    await service.note_turn(conv.id)
    await service.note_turn(conv.id)
    (row,) = await service.list_rows("web")
    assert row.record.turn_count == 2
    assert row.record.last_turn_at is not None
    assert row.client_name == "WEB"


async def test_list_puts_the_last_spoken_first(service):
    old, new = await service.create("web", "main"), await service.create("tv", "main")
    await service.note_turn(old.id)
    rows = await service.list_rows()
    assert [r.record.id for r in rows] == [old.id, new.id]
    assert [r.record.id for r in await service.list_rows("tv")] == [new.id]


async def test_load_fills_the_index(service, engine):
    conv = await service.create("web", "main")
    fresh = ConversationService(engine)
    assert fresh.index.get(conv.session_key) is None
    await fresh.load()
    assert fresh.index.get(conv.session_key) == conv


async def test_deleting_a_client_drops_its_conversations(service, engine):
    conv = await service.create("web", "main")
    with Session(engine) as s:
        service.delete_client(s, "web")
        s.commit()
    assert await service.get(conv.id) is None
    assert service.index.get(conv.session_key) is None


# ── Sesiones de antes de M19 ──────────────────────────────────────────────


class FakeOpenClaw:
    def __init__(self, rows, page=None):
        self.rows = rows
        self.page = page
        self.calls = []

    async def request(self, method, params, timeout=10.0):
        self.calls.append((method, params))
        offset, limit = params.get("offset", 0), self.page or params["limit"]
        page = self.rows[offset:offset + limit]
        more = offset + limit < len(self.rows)
        return {"sessions": page, "hasMore": more, "nextOffset": offset + limit if more else None}


async def test_legacy_sessions_are_imported_once(service, engine):
    rows = [
        {"key": "agent:main:tv", "createdAt": 1_700_000_000_000, "updatedAt": 1_700_000_100_000},
        {"key": "agent:jota:tv"},
        {"key": "agent:main:unknown-client"},  # sin cliente: no es nuestra
        {"key": "agent:main:fogar-xyz"},  # ya es de M19
        {"key": "agent:main:telegram:direct:42"},  # de otro canal
        {"key": "global"},
    ]
    openclaw = FakeOpenClaw(rows, page=2)
    assert await service.import_legacy(openclaw) == 2
    assert await service.import_legacy(openclaw) == 0  # idempotente
    with Session(engine) as s:
        records = s.exec(select(ConversationRecord)).all()
    assert sorted((r.session_key, r.client_id, r.agent, r.kind) for r in records) == [
        ("agent:jota:tv", "tv", "jota", "legacy"),
        ("agent:main:tv", "tv", "main", "legacy"),
    ]
    main = next(r for r in records if r.agent == "main")
    assert main.created_at.replace(tzinfo=UTC) == datetime.fromtimestamp(1_700_000_000, UTC)
    assert main.day is None and main.turn_count == 0
    assert service.index.get("agent:main:tv").client_id == "tv"
    # Paginado y con archivadas, sin pedir títulos.
    assert all(p["archived"] == "all" and p["rowMode"] == "compact" for _, p in openclaw.calls)


async def test_legacy_import_retries_after_a_failure(service):
    class Down:
        calls = 0

        async def request(self, method, params, timeout=10.0):
            Down.calls += 1
            if Down.calls == 1:
                raise RuntimeError("down")
            return {"sessions": [{"key": "agent:main:tv"}]}

    job = LegacyImport(service, Down())
    job.kick()
    await job.wait()
    assert not job.done
    job.kick()
    await job.wait()
    assert job.done
    job.kick()  # hecha: no vuelve a preguntar
    await job.wait()
    assert Down.calls == 2


# ── Lectura de OpenClaw ───────────────────────────────────────────────────


def test_history_is_normalised_to_role_text_and_time():
    raw = [
        {"role": "user", "content": "hola", "timestamp": 1_794_665_071_190},
        {"role": "assistant", "content": [{"type": "text", "text": "¿qué tal?"},
                                          {"type": "toolCall", "name": "x"}], "timestamp": 1},
        {"role": "assistant", "content": [{"type": "toolCall", "name": "x"}]},  # sin texto
        {"role": "toolResult", "content": "secret"},
        {"role": "custom", "customType": "run-failed-before-reply", "content": "fallo"},
        "basura",
    ]
    assert history_messages(raw) == [
        {"role": "user", "text": "hola",
         "timestamp": datetime.fromtimestamp(1_794_665_071.19, UTC)},
        {"role": "assistant", "text": "¿qué tal?", "timestamp": datetime.fromtimestamp(0.001, UTC)},
    ]
    assert history_messages(None) == []


def test_title_prefers_the_derived_one():
    assert session_title({"derivedTitle": " Luces ", "displayName": "x"}) == "Luces"
    assert session_title({"displayName": "Cena", "label": "y"}) == "Cena"
    assert session_title({"label": "z"}) == "z"
    assert session_title({"derivedTitle": "  ", "key": "k"}) is None


# ── Cambio de día de una sesión ───────────────────────────────────────────


async def test_a_default_session_moves_to_the_new_day(service, clock):
    first = await service.default_for("tv", "main")
    session = SessionConversation(first, explicit=False, service=service)
    moves = []
    session.on_move = lambda old, new: moves.append((old.id, new.id))
    await session.refresh()
    assert session.current == first and moves == []  # mismo día: nada
    clock.now = datetime(2026, 11, 15, 6, 0)
    await session.refresh()
    assert session.current.day == date(2026, 11, 15)
    assert moves == [(first.id, session.id)]
    assert session.id_for_key(first.session_key) == first.id  # ayer sigue en el índice


@pytest.mark.parametrize("make", ["created", "explicit_default"])
async def test_a_chosen_conversation_never_changes_day(service, clock, make):
    if make == "created":
        conv, explicit = await service.create("tv", "main"), True
    else:
        conv, explicit = await service.default_for("tv", "main"), True
    session = SessionConversation(conv, explicit=explicit, service=service)
    clock.now = datetime(2026, 11, 20, 12, 0)
    await session.refresh()
    assert session.current == conv


async def test_without_db_the_turn_stays_in_its_conversation(service, clock, caplog):
    first = await service.default_for("tv", "main")
    session = SessionConversation(first, explicit=False, service=service)
    clock.now = datetime(2026, 11, 15, 6, 0)

    async def broken(*a, **k):
        raise RuntimeError("database is locked")

    service.default_for = broken
    await session.refresh()
    assert session.current == first
    assert "database is locked" not in caplog.text


# ── Sesiones vivas y reparto del push ─────────────────────────────────────


def _conv(cid, client="tv", agent="main"):
    return Conversation(cid, client, agent, session_key_for(agent, cid), "default", date(2026, 11, 14))


def test_a_conversation_is_open_in_one_session_only():
    reg = ClientRegistry()
    old, new = object(), object()
    assert reg.register("tv", old, _conv("c1")) is None
    assert reg.register("tv", new, _conv("c1")) is old  # el llamador cierra la vieja
    assert reg.holder("c1") is new
    reg.unregister("tv", old)  # el cierre tardío de la vieja no quita a la nueva
    assert reg.holder("c1") is new and reg.get("tv") is new


def test_push_goes_to_the_session_holding_its_conversation():
    reg = ClientRegistry()
    a, b = object(), object()
    reg.register("web", a, _conv("c1", "web"))
    reg.register("web", b, _conv("c2", "web"))
    assert reg.route(session_key_for("main", "c1")) is a
    assert reg.route(session_key_for("main", "c2")) is b
    assert sorted(map(id, reg.bridges())) == sorted([id(a), id(b)])


def test_push_of_a_conversation_not_open_goes_to_the_clients_last_session():
    """La conversación de ayer de la tele: su sesión ya está en la de hoy."""
    reg = ClientRegistry()
    tv = object()
    reg.index.add(_conv("yesterday"))
    reg.register("tv", tv, _conv("today"))
    assert reg.route(session_key_for("main", "yesterday")) is tv
    reg.unregister("tv", tv)
    assert reg.route(session_key_for("main", "yesterday")) is None


def test_push_of_a_key_not_yet_imported_goes_by_its_client_id():
    reg = ClientRegistry()
    tv = object()
    reg.register("tv", tv, _conv("today"))
    assert reg.route("agent:main:tv") is tv
    assert reg.route("agent:main:nobody") is None


def test_moving_a_session_to_another_conversation():
    reg = ClientRegistry()
    tv = object()
    reg.register("tv", tv, _conv("d1"))
    reg.close("d1", tv)
    assert reg.open(_conv("d2"), tv) is None
    assert reg.holder("d1") is None and reg.holder("d2") is tv
