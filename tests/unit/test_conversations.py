"""Conversaciones (M19): reglas del día, la tabla, la importación de antes de M19, el índice
y el reparto del push (`ClientRegistry`). services/conversations.py."""

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from fogar_gateway.core.config import settings
from fogar_gateway.db.models import ClientRecord, ConversationRecord
from fogar_gateway.services import conversations as conv_mod
from fogar_gateway.services.conversations import (
    Conversation,
    ConversationClientMissing,
    ConversationNotFound,
    ConversationService,
    LegacyImport,
    SessionConversation,
    TitleCache,
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


async def test_deleting_a_client_cascades_to_its_conversations(service, engine):
    """PRAGMA foreign_keys=ON en cada conexión (db/database.py): ON DELETE CASCADE vale."""
    conv = await service.create("web", "main")
    with Session(engine) as s:
        s.delete(s.get(ClientRecord, "web"))
        s.commit()
    assert await service.get(conv.id) is None
    assert await service.list_rows() == []


async def test_a_conversation_for_a_missing_client_fails_cleanly(service, clock):
    """La clave foránea rechaza el alta (p. ej. el cliente se borró a la vez)."""
    with pytest.raises(ConversationClientMissing):
        await service.default_for("borrado", "main")
    with pytest.raises(ConversationClientMissing):
        await service.create("borrado", "main")
    assert service.index.get("agent:main:fogar-x") is None


async def test_list_hides_default_conversations_nobody_spoke_in(service, clock):
    silent = await service.default_for("tv", "main")
    spoken = await service.default_for("web", "main")
    created = await service.create("tv", "main")  # una elegida sí sale, aunque esté vacía
    await service.note_turn(spoken.id)
    assert {r.record.id for r in await service.list_rows()} == {spoken.id, created.id}
    assert (await service.row(silent.id)) is not None  # por id, sí


async def test_titles_are_cached_and_failures_are_not():
    reads = []

    class OpenClaw:
        fail = False

        async def request(self, method, params, timeout=10.0):
            reads.append(method)
            if OpenClaw.fail:
                raise RuntimeError("down")
            return {"sessions": [{"key": "k", "derivedTitle": "Cena"}], "hasMore": False}

    cache = TitleCache()
    assert await cache.get(OpenClaw()) == {"k": "Cena"}
    assert await cache.get(OpenClaw()) == {"k": "Cena"}
    assert reads == ["sessions.list"]  # la segunda, de la caché
    cache._at = None  # caducada
    OpenClaw.fail = True
    assert await cache.get(OpenClaw()) == {}
    assert await cache.get(OpenClaw()) == {}
    assert len(reads) == 2  # un fallo se recuerda un rato: la segunda no pregunta
    cache._failed_at -= 60  # pasado TITLES_FAILURE_TTL_S
    assert await cache.get(OpenClaw()) == {}
    assert len(reads) == 3


async def test_concurrent_title_requests_share_one_read():
    started, release = asyncio.Event(), asyncio.Event()
    reads = []

    class Hanging:
        async def request(self, method, params, timeout=10.0):
            reads.append(method)
            started.set()
            await release.wait()
            raise TimeoutError()

    cache = TitleCache()
    waiters = [asyncio.create_task(cache.get(Hanging())) for _ in range(5)]
    await started.wait()
    waiters[0].cancel()  # una petición que se va no cancela la lectura de las demás
    release.set()
    results = await asyncio.gather(*waiters[1:])
    assert results == [{}] * 4 and reads == ["sessions.list"]
    assert await cache.get(Hanging()) == {} and reads == ["sessions.list"]  # fallo recordado


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


async def test_a_client_deleted_mid_import_does_not_fail_the_others(service, engine, monkeypatch):
    """Con claves foráneas, un commit por cliente: uno borrado entre la lectura de clientes y
    su alta no se importa, y los demás sí."""
    real = conv_mod._legacy_key
    # La importación leyó los dos clientes; `web` se borra antes de su commit.
    monkeypatch.setattr(conv_mod, "_legacy_key", lambda key, _ids: real(key, {"tv", "web"}))
    with Session(engine) as s:
        s.delete(s.get(ClientRecord, "web"))
        s.commit()
    imported = service._import_legacy([{"key": "agent:main:tv"}, {"key": "agent:main:web"}])
    assert [(c.session_key, c.client_id) for c in imported] == [("agent:main:tv", "tv")]


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

    async def on_move(old, new):
        moves.append((old.id, new.id))

    session.on_move = on_move
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


async def test_a_day_move_frees_the_old_keys_queue_peak_and_keeps_its_value(make_bridge):
    bridge, _ = make_bridge()
    old = bridge.conversation.current
    new = Conversation("d2", old.client_id, "main", session_key_for("main", "d2"), "default", None)
    bridge._turn_registry._peak[old.session_key] = 3
    await bridge._on_conversation_move(old, new)
    assert old.session_key not in bridge._turn_registry._peak
    assert bridge._session_end_meta("completed")["turn_queue_peak"] == 3


async def test_a_session_that_lost_its_conversation_never_aborts_on_that_key(make_bridge):
    """Red de seguridad del desplazamiento: ya no aborta en la clave que otra sesión tiene."""
    from tests.fakes.bridge import push

    bridge, _ = make_bridge()
    await push(bridge, ("turn_start", bridge.conversation.session_key))
    bridge.conversation.released = True
    assert (await bridge.turns.cancel("cancelled"))[0] is True
    bridge.orchestrator.abort.assert_not_awaited()
    await bridge.close_all()


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
