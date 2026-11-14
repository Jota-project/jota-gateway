"""Conversaciones (M19): reglas, índice en memoria y acceso a la BD.

Una conversación es una sesión de OpenClaw con clave `agent:{agent}:fogar-{id}` (las importadas
de antes de M19 conservan la suya, `agent:{agent}:{client.id}`). La tabla `conversations` es solo
su índice: lo dicho y el título viven en OpenClaw (`chat.history`, `sessions.list`).

Reglas, iguales para todos los clientes (el gateway no mira el tipo de cliente):

- Un cliente puede elegir una de **sus** conversaciones o crear una (`conversation` en el
  handshake: un id o `"new"`). Esa sesión no cambia nunca de conversación por sí sola.
- Quien no elige cae en la **conversación por defecto**: una por cliente, agente y día. El día
  empieza a las `CONVERSATION_DAY_START_HOUR` (hora local del host) y se evalúa al empezar cada
  turno: una sesión abierta días (la tele) pasa a la del día nuevo en su primer turno tras el corte.
- Una conversación está abierta en una sola sesión (`ClientRegistry`): abrirla en otra cierra la
  anterior con 1008 «conversation opened elsewhere».
- El push se enruta por su `sessionKey`: índice → conversación → sesión viva (`ClientRegistry`).

Nada de esto guarda ni registra texto del usuario.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from fogar_gateway.core.config import settings
from fogar_gateway.db.database import get_engine
from fogar_gateway.db.models import ClientRecord, ConversationRecord
from fogar_gateway.services.openclaw.chat_text import extract_message_text
from fogar_gateway.services.reconnection import describe_error, traceback_no_message
from fogar_gateway.services.teardown import Teardown

logger = logging.getLogger(__name__)

Kind = Literal["default", "created", "legacy"]
# `conversation` del handshake que pide una conversación nueva.
NEW = "new"
_KEY_PREFIX = "fogar-"

# Reloj de pared local del host (sin zona), como el `atHour` de OpenClaw. Los tests lo cambian.
_now: Callable[[], datetime] = datetime.now

# sessions.list: filas por página y tope de páginas (importación y títulos).
_LIST_PAGE = 500
_LIST_MAX_PAGES = 20


def session_key_for(agent: str, conversation_id: str) -> str:
    return f"agent:{agent}:{_KEY_PREFIX}{conversation_id}"


def conversation_day(now: datetime | None = None) -> date:
    """El día de las conversaciones por defecto: empieza a las CONVERSATION_DAY_START_HOUR."""
    now = now if now is not None else _now()
    return (now - timedelta(hours=settings.CONVERSATION_DAY_START_HOUR)).date()


@dataclass(frozen=True)
class Conversation:
    id: str
    client_id: str
    agent: str
    session_key: str
    kind: Kind
    day: date | None = None

    @classmethod
    def from_record(cls, r: ConversationRecord) -> "Conversation":
        return cls(r.id, r.client_id, r.agent, r.session_key, r.kind, r.day)  # type: ignore[arg-type]


class ConversationNotFound(Exception):
    """El id no existe o es de otro cliente (no se distingue: no se revela que existe)."""


class ConversationClientMissing(Exception):
    """El cliente ya no existe (borrado a la vez): la clave foránea rechazó el alta."""


class _Requester(Protocol):
    async def request(self, method: str, params: dict, timeout: float = ...) -> dict: ...


class ConversationIndex:
    """`session_key` → conversación, en memoria: cargado de la BD al arrancar y al día con cada
    alta. Lo usa el reparto de push (`ClientRegistry.route`). Solo se toca desde el hilo del
    loop (los métodos async de ConversationService lo actualizan al volver del hilo de la BD)."""

    def __init__(self) -> None:
        self._by_key: dict[str, Conversation] = {}

    def add(self, conversation: Conversation) -> None:
        self._by_key[conversation.session_key] = conversation

    def get(self, session_key: str) -> Conversation | None:
        return self._by_key.get(session_key)

    def replace_all(self, conversations: list[Conversation]) -> None:
        self._by_key = {c.session_key: c for c in conversations}

    def forget_client(self, client_id: str) -> None:
        self._by_key = {k: c for k, c in self._by_key.items() if c.client_id != client_id}

    def __len__(self) -> int:
        return len(self._by_key)


@dataclass(frozen=True)
class ConversationRow:
    """Una fila de `/admin/conversations` (sin título: lo pone la ruta desde OpenClaw)."""

    record: ConversationRecord
    client_name: str


class ConversationService:
    """La tabla `conversations` y su índice. Toda la E/S de SQLite va a un hilo (como db_client)."""

    def __init__(self, engine: Any = None) -> None:
        self._engine = engine
        self.index = ConversationIndex()

    def _get_engine(self) -> Any:
        return self._engine if self._engine is not None else get_engine()

    # ── Lectura y alta ────────────────────────────────────────────────────

    async def load(self) -> None:
        """Carga el índice desde la BD (al arrancar)."""
        rows = await asyncio.to_thread(self._load)
        self.index.replace_all(rows)

    def _load(self) -> list[Conversation]:
        with Session(self._get_engine()) as s:
            return [Conversation.from_record(r) for r in s.exec(select(ConversationRecord)).all()]

    async def default_for(self, client_id: str, agent: str, day: date | None = None) -> Conversation:
        """La conversación por defecto de (cliente, agente, día); la crea si no existe."""
        day = day if day is not None else conversation_day()
        conv = await asyncio.to_thread(self._default_for, client_id, agent, day)
        self.index.add(conv)
        return conv

    def _default_for(self, client_id: str, agent: str, day: date) -> Conversation:
        with Session(self._get_engine()) as s:
            found = self._find_default(s, client_id, agent, day)
            if found is not None:
                return Conversation.from_record(found)
            record = _new_record(client_id, agent, "default", day)
            s.add(record)
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                _require_client(s, client_id)
                # Otra sesión del mismo cliente la creó a la vez (índice único): es esa.
                found = self._find_default(s, client_id, agent, day)
                if found is None:
                    raise
                return Conversation.from_record(found)
            s.refresh(record)
            return Conversation.from_record(record)

    @staticmethod
    def _find_default(s: Session, client_id: str, agent: str, day: date) -> ConversationRecord | None:
        return s.exec(
            select(ConversationRecord).where(
                ConversationRecord.client_id == client_id,
                ConversationRecord.agent == agent,
                ConversationRecord.day == day,
                ConversationRecord.kind == "default",
            )
        ).first()

    async def create(self, client_id: str, agent: str) -> Conversation:
        """Una conversación nueva (`kind: created`) del cliente."""
        conv = await asyncio.to_thread(self._create, client_id, agent)
        self.index.add(conv)
        return conv

    def _create(self, client_id: str, agent: str) -> Conversation:
        with Session(self._get_engine()) as s:
            record = _new_record(client_id, agent, "created", None)
            s.add(record)
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                _require_client(s, client_id)
                raise
            s.refresh(record)
            return Conversation.from_record(record)

    async def get(self, conversation_id: str) -> Conversation | None:
        return await asyncio.to_thread(self._get, conversation_id)

    def _get(self, conversation_id: str) -> Conversation | None:
        with Session(self._get_engine()) as s:
            record = s.get(ConversationRecord, conversation_id)
            return Conversation.from_record(record) if record is not None else None

    async def get_owned(self, client_id: str, conversation_id: str) -> Conversation:
        """La conversación `conversation_id` si es del cliente; si no, ConversationNotFound."""
        conv = await self.get(conversation_id)
        if conv is None or conv.client_id != client_id:
            raise ConversationNotFound()
        self.index.add(conv)
        return conv

    async def note_turn(self, conversation_id: str) -> None:
        """Un turno empieza en la conversación: `turn_count + 1` y `last_turn_at`."""
        await asyncio.to_thread(self._note_turn, conversation_id, datetime.now(UTC))

    def _note_turn(self, conversation_id: str, at: datetime) -> None:
        with Session(self._get_engine()) as s:
            s.exec(  # type: ignore[call-overload]
                update(ConversationRecord)
                .where(col(ConversationRecord.id) == conversation_id)
                .values(turn_count=ConversationRecord.turn_count + 1, last_turn_at=at)
            )
            s.commit()

    async def list_rows(self, client_id: str | None = None) -> list[ConversationRow]:
        """Todas (o las de un cliente), la última en hablarse primero. Sin las del día en
        las que nunca se habló: las crea el handshake de un cliente que conecta y calla."""
        return await asyncio.to_thread(self._list, client_id, None)

    async def row(self, conversation_id: str) -> ConversationRow | None:
        rows = await asyncio.to_thread(self._list, None, conversation_id)
        return rows[0] if rows else None

    def _list(self, client_id: str | None, conversation_id: str | None) -> list[ConversationRow]:
        with Session(self._get_engine()) as s:
            query = select(ConversationRecord, ClientRecord.name).join(
                ClientRecord, col(ClientRecord.id) == ConversationRecord.client_id
            )
            if client_id is not None:
                query = query.where(ConversationRecord.client_id == client_id)
            if conversation_id is not None:
                query = query.where(ConversationRecord.id == conversation_id)
            else:
                query = query.where(
                    or_(col(ConversationRecord.kind) != "default", col(ConversationRecord.turn_count) > 0)
                )
            rows = [ConversationRow(record, name) for record, name in s.exec(query).all()]
        rows.sort(key=lambda r: _utc(r.record.last_turn_at or r.record.created_at), reverse=True)
        return rows

    # ── Sesiones de antes de M19 ──────────────────────────────────────────

    async def import_legacy(self, orchestrator: _Requester) -> int:
        """Importa como `legacy` cada sesión de OpenClaw `agent:{agent}:{client.id}` de un
        cliente que existe. Idempotente: una clave ya importada no se repite."""
        rows = await list_openclaw_sessions(orchestrator, compact=True)
        imported = await asyncio.to_thread(self._import_legacy, rows)
        for conv in imported:
            self.index.add(conv)
        return len(imported)

    def _import_legacy(self, rows: list[dict]) -> list[Conversation]:
        """Un commit por cliente: uno borrado a mitad (la clave foránea rechaza sus filas) no
        tumba la importación de los demás; las suyas, simplemente, no se importan."""
        with Session(self._get_engine()) as s:
            client_ids = set(s.exec(select(ClientRecord.id)).all())
            known = set(s.exec(select(ConversationRecord.session_key)).all())
        by_client: dict[str, list[ConversationRecord]] = {}
        for row in rows:
            parsed = _legacy_key(row.get("key"), client_ids)
            if parsed is None or row["key"] in known:
                continue
            agent, client_id = parsed
            record = _new_record(client_id, agent, "legacy", None, session_key=row["key"])
            created = _ms_to_datetime(row.get("createdAt"))
            if created is not None:
                record.created_at = created
            record.last_turn_at = _ms_to_datetime(row.get("lastInteractionAt") or row.get("updatedAt"))
            known.add(record.session_key)
            by_client.setdefault(client_id, []).append(record)
        imported: list[Conversation] = []
        for client_id, records in by_client.items():
            with Session(self._get_engine()) as s:
                s.add_all(records)
                try:
                    s.commit()
                except IntegrityError:
                    s.rollback()
                    logger.info(f"Importación legacy: el cliente {client_id} ya no existe; se omite")
                    continue
                for record in records:
                    s.refresh(record)
                imported.extend(Conversation.from_record(r) for r in records)
        return imported


def _require_client(s: Session, client_id: str) -> None:
    """Tras un IntegrityError: si el cliente ya no existe, fue la clave foránea."""
    if s.get(ClientRecord, client_id) is None:
        raise ConversationClientMissing() from None


def _new_record(
    client_id: str, agent: str, kind: Kind, day: date | None, *, session_key: str | None = None
) -> ConversationRecord:
    record = ConversationRecord(client_id=client_id, agent=agent, kind=kind, day=day, session_key="")
    record.session_key = session_key or session_key_for(agent, record.id)
    return record


def _legacy_key(key: object, client_ids: set[str]) -> tuple[str, str] | None:
    """(agente, client_id) de una clave `agent:{agent}:{client.id}` de un cliente conocido."""
    if not isinstance(key, str):
        return None
    parts = key.split(":")
    if len(parts) != 3 or parts[0] != "agent" or not parts[1]:
        return None
    _, agent, client_id = parts
    if client_id.startswith(_KEY_PREFIX) or client_id not in client_ids:
        return None
    return agent, client_id


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _ms_to_datetime(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


async def list_openclaw_sessions(orchestrator: _Requester, *, compact: bool = False) -> list[dict]:
    """Todas las sesiones de OpenClaw (`sessions.list`, archivadas incluidas), paginando.

    Sin `compact`, cada fila trae su título (`includeDerivedTitles`). Lanza lo que lance
    `orchestrator.request` (OpenClaw caído, rechazo)."""
    rows: list[dict] = []
    offset = 0
    for _ in range(_LIST_MAX_PAGES):
        params: dict[str, Any] = {"limit": _LIST_PAGE, "offset": offset, "archived": "all"}
        if compact:
            params["rowMode"] = "compact"
        else:
            params["includeDerivedTitles"] = True
        payload = await orchestrator.request("sessions.list", params)
        page = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(page, list):
            break
        rows.extend(r for r in page if isinstance(r, dict) and isinstance(r.get("key"), str))
        next_offset = payload.get("nextOffset")
        if not payload.get("hasMore") or not isinstance(next_offset, int) or next_offset <= offset:
            break
        offset = next_offset
    return rows


# Vida de los títulos leídos de OpenClaw (como la caché de /admin/access): la lista de
# conversaciones no recorre `sessions.list` entero en cada petición.
TITLES_TTL_S = 20.0
# Lo que se recuerda un fallo al leerlos (OpenClaw caído o colgado).
TITLES_FAILURE_TTL_S = 5.0


class TitleCache:
    """`sessionKey` → título, de `sessions.list` (con títulos derivados), cacheado TITLES_TTL_S.

    Las peticiones a la vez esperan la misma lectura (una tarea compartida: si una petición se
    cancela, la lectura sigue para las demás). Un fallo se recuerda TITLES_FAILURE_TTL_S: con
    OpenClaw colgado, las listas salen enseguida sin títulos en vez de esperar cada una su tope."""

    def __init__(self) -> None:
        self._titles: dict[str, str] = {}
        self._at: float | None = None
        self._failed_at: float | None = None
        self._fetch: asyncio.Task | None = None

    async def get(self, orchestrator: _Requester) -> dict[str, str]:
        now = time.monotonic()
        if self._at is not None and now - self._at < TITLES_TTL_S:
            return self._titles
        if self._failed_at is not None and now - self._failed_at < TITLES_FAILURE_TTL_S:
            return {}
        if self._fetch is None or self._fetch.done():
            self._fetch = asyncio.create_task(self._read(orchestrator), name="conversation_titles")
        return await asyncio.shield(self._fetch)

    async def _read(self, orchestrator: _Requester) -> dict[str, str]:
        try:
            rows = await list_openclaw_sessions(orchestrator)
        except Exception as e:  # noqa: BLE001 — frontera: sin OpenClaw la lista sale sin títulos
            logger.info(f"Conversaciones sin títulos: OpenClaw no responde ({describe_error(e)})")
            self._failed_at = time.monotonic()
            return {}
        self._titles = {r["key"]: t for r in rows if (t := session_title(r)) is not None}
        self._at, self._failed_at = time.monotonic(), None
        return self._titles

    async def aclose(self) -> None:
        async with Teardown("títulos de conversaciones") as td:
            await td.cancel(self._fetch, "lectura de títulos", 5.0)


def session_title(row: dict) -> str | None:
    """El título de una fila de `sessions.list`: el que muestra OpenClaw (`derivedTitle`,
    `displayName` o `label`, en ese orden), o None."""
    for field in ("derivedTitle", "displayName", "label"):
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def history_messages(raw: object) -> list[dict]:
    """Los mensajes de `chat.history` normalizados a `{role, text, timestamp}`: solo `user` y
    `assistant` con texto (sin herramientas, avisos ni filas vacías), del más antiguo al último."""
    out = []
    for message in raw if isinstance(raw, list) else []:
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            continue
        text = extract_message_text(message).strip()
        if text:
            timestamp = _ms_to_datetime(message.get("timestamp"))
            out.append({"role": message["role"], "text": text, "timestamp": timestamp})
    return out


class SessionConversation:
    """La conversación de una sesión viva y su cambio de día (lo usa `TurnController`).

    `explicit`: la sesión eligió conversación en el handshake; nunca cambia sola. Si no, en una
    conversación por defecto, `refresh()` (al empezar cada turno del usuario) la pasa a la del
    día de hoy y avisa con `on_move(anterior, nueva)`.
    """

    def __init__(
        self,
        conversation: Conversation,
        *,
        explicit: bool,
        service: ConversationService | None = None,
        index: ConversationIndex | None = None,
    ) -> None:
        self.current = conversation
        self._explicit = explicit
        self._service = service
        self._index = index if index is not None else (service.index if service else None)
        self.on_move: Callable[[Conversation, Conversation], Awaitable[None]] | None = None
        # La sesión perdió su conversación (otra la abrió): ya no aborta nada en su clave.
        self.released = False
        self._spawn: Callable[[Coroutine[Any, Any, None], str], None] | None = None

    @property
    def id(self) -> str:
        return self.current.id

    @property
    def session_key(self) -> str:
        return self.current.session_key

    def bind(self, spawn: Callable[[Coroutine[Any, Any, None], str], None]) -> None:
        """Dónde lanzar las escrituras en segundo plano (`_spawn_background` del bridge)."""
        self._spawn = spawn

    def id_for_key(self, session_key: str) -> str | None:
        """La conversación de una `sessionKey` (la de un push); None si no se conoce."""
        if session_key == self.current.session_key:
            return self.current.id
        conv = self._index.get(session_key) if self._index is not None else None
        return conv.id if conv is not None else None

    async def refresh(self) -> None:
        """Pasa a la conversación por defecto de hoy si el día cambió. Nunca lanza Exception:
        sin BD el turno sigue en la conversación actual."""
        conv = self.current
        if self._explicit or conv.kind != "default" or self._service is None:
            return
        today = conversation_day()
        if conv.day == today:
            return
        try:
            new = await self._service.default_for(conv.client_id, conv.agent, today)
        except Exception as e:  # noqa: BLE001 — frontera: el turno sigue en la conversación actual
            logger.warning(
                f"[{conv.client_id}] Cambio de día de la conversación fallido "
                f"({describe_error(e)}); el turno sigue en {conv.id}."
            )
            return
        if new.id == conv.id:
            return
        self.current = new
        logger.info(f"[{conv.client_id}] Conversación del día: {conv.id} -> {new.id}")
        if self.on_move is not None:
            await self.on_move(conv, new)

    def note_turn(self, conversation_id: str | None) -> None:
        """Cuenta un turno de `conversation_id` en la BD, en segundo plano."""
        if conversation_id is None or self._service is None or self._spawn is None:
            return
        self._spawn(note_turn_safely(self._service, conversation_id), "conversation_turn")


async def note_turn_safely(service: ConversationService, conversation_id: str) -> None:
    """`service.note_turn`, sin lanzar Exception: un contador no rompe un turno."""
    try:
        await service.note_turn(conversation_id)
    except Exception as e:  # noqa: BLE001 — frontera: el contador nunca rompe el turno
        logger.warning(f"No se pudo anotar el turno de {conversation_id} ({describe_error(e)})")


class LegacyImport:
    """Importa una vez (por proceso) las sesiones de antes de M19 cuando OpenClaw está conectado.

    `kick()` no bloquea: lanza la importación si no se hizo ya ni está en marcha. Si falla
    (OpenClaw caído a mitad), el siguiente `kick()` (la próxima conexión) la reintenta.
    """

    def __init__(self, service: ConversationService, orchestrator: _Requester) -> None:
        self._service = service
        self._orchestrator = orchestrator
        self._task: asyncio.Task | None = None
        self.done = False

    def kick(self) -> None:
        if self.done or (self._task is not None and not self._task.done()):
            return
        self._task = asyncio.create_task(self._run(), name="legacy_conversations_import")

    async def wait(self) -> None:
        """Espera la importación en marcha (para tests)."""
        if self._task is not None:
            await asyncio.wait({self._task})

    async def _run(self) -> None:
        try:
            count = await self._service.import_legacy(self._orchestrator)
        except Exception as e:  # noqa: BLE001 — frontera: se reintenta en la próxima conexión
            logger.warning(f"Importación de conversaciones anteriores fallida ({describe_error(e)})")
            logger.debug(f"importación: {traceback_no_message(e)}")
            return
        self.done = True
        if count:
            logger.info(f"Importadas {count} conversaciones anteriores a M19 (legacy)")

    async def aclose(self) -> None:
        async with Teardown("importación de conversaciones") as td:
            await td.cancel(self._task, "importación", 5.0)
