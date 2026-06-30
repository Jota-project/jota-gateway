# API Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the jota-gateway API surface — remove dead jota-db proxy routes, fix health endpoints, create `/admin/*` with observability + CRUD stubs, implement typed WebSocket protocol (ready, turn_start/end, token, error, status, binary audio framing).

**Architecture:** Three clean surfaces: WS `/ws/stream` (typed protocol), HTTP `/v1/*` (OpenAI-compat, no auth), HTTP `/admin/*` (X-Admin-Token). The `/api/*` prefix disappears entirely. WebSocket gains lifecycle messages and standardized `type`-keyed payloads. Binary audio output gets a 3-byte header `[0xA1][turn_seq uint16 BE]` to identify turns.

**Tech Stack:** FastAPI, Python 3.12+, Pydantic v2, pytest, respx, Starlette TestClient

**Spec:** `docs/superpowers/specs/2026-06-30-api-redesign-design.md`

## Global Constraints

- Python 3.12+, all new code type-annotated
- Run tests: `PYTHONPATH=. pytest`
- Run single test: `PYTHONPATH=. pytest tests/path/test.py::test_name -v`
- No new external dependencies
- `src/services/db_client.py` is NOT touched — separate session handles DB migration
- Commit after every task

---

## File Map

**Delete:**
- `src/api/conversation_routes.py`
- `src/api/models_routes.py`
- `src/api/config_routes.py`
- `src/api/orchestrator_routes.py` (content moves to admin_routes.py)
- `src/api/sessions_routes.py` (content moves to admin_routes.py)
- `tests/integration/test_rest_conversations.py`
- `tests/integration/test_rest_models.py`
- `tests/integration/test_rest_config.py`

**Create:**
- `src/api/admin_routes.py`

**Modify:**
- `src/core/config.py` — add `ADMIN_TOKEN`
- `src/api/deps.py` — replace `get_verified_client` with `get_admin_auth`
- `src/api/health_routes.py` — rewrite as `/healthz` + `/ready`
- `src/api/openai_routes.py` — fix `/v1/models` static response
- `src/api/routes.py` — send `ready` after health check
- `src/services/bridge.py` — full typed protocol + binary framing
- `src/services/openclaw/reconnecting.py` — add `get_name()` method
- `src/main.py` — register new routers, remove old ones
- `tests/integration/conftest.py` — add admin token fixtures, simplify mocks
- `tests/integration/test_rest_health.py` — rewrite for new endpoints
- `tests/integration/test_orchestrator_routes.py` — new paths + admin auth
- `tests/integration/test_sessions_api.py` — new paths + admin auth
- `tests/integration/test_ws_handshake.py` — consume `ready` message
- `tests/integration/test_bridge_flow.py` — consume `ready` + `turn_start`, new token format
- `tests/integration/test_bridge_audio_flow.py` — consume `ready` + `turn_start`, new formats
- `tests/unit/test_bridge_health_check.py` — `state` not `status`
- `tests/unit/test_bridge_barge_in.py` — `status` type not `service_status`
- `tests/unit/test_bridge_push.py` — `token`/`text` not `push`/`content`, binary framing

---

## Task 1: Remove dead routes

**Files:**
- Delete: `src/api/conversation_routes.py`, `src/api/models_routes.py`, `src/api/config_routes.py`
- Delete: `tests/integration/test_rest_conversations.py`, `tests/integration/test_rest_models.py`, `tests/integration/test_rest_config.py`
- Modify: `src/main.py`
- Modify: `src/api/deps.py`
- Modify: `tests/integration/conftest.py`

**Interfaces:**
- Produces: `src/main.py` with dead routers removed; `deps.py` without `get_verified_client`

- [ ] **Step 1: Delete dead route files and test files**

```bash
rm src/api/conversation_routes.py src/api/models_routes.py src/api/config_routes.py
rm tests/integration/test_rest_conversations.py tests/integration/test_rest_models.py tests/integration/test_rest_config.py
```

- [ ] **Step 2: Update `src/main.py` — remove dead router imports and registrations**

Remove lines importing and registering `config_router`, `conversation_router`, `models_router`. Also remove the inline `GET /health` at the bottom (replaced in Task 2). Final result:

```python
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.api.routes import router as stream_router
from src.api.health_routes import router as health_router
from src.api.openai_routes import router as openai_router
from src.api.orchestrator_routes import router as orchestrator_router
from src.api.sessions_routes import router as sessions_router
from src.core.config import settings
from src.services.db_client import db_client
from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.reconnecting import ReconnectingOpenClawClient
from src.services.openclaw.registry import TurnRegistry, ClientRegistry
from src.services.session_registry import SessionRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_client.connect()

    turn_registry = TurnRegistry()
    client_registry = ClientRegistry()
    dispatcher = FrameDispatcher(turn_registry, client_registry)
    inner = OpenClawClient(
        host=settings.OPENCLAW_HOST,
        port=settings.OPENCLAW_PORT,
        token=settings.OPENCLAW_TOKEN,
        turn_registry=turn_registry,
        dispatcher=dispatcher,
    )
    openclaw = ReconnectingOpenClawClient(
        inner,
        name="openclaw",
        initial_backoff=settings.ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF,
        max_backoff=settings.ORCHESTRATOR_RECONNECT_MAX_BACKOFF,
        max_duration=settings.ORCHESTRATOR_RECONNECT_MAX_DURATION,
    )
    try:
        await openclaw.connect()
    except Exception as e:
        logger.error(f"Initial OpenClaw connect failed: {e}")

    app.state.openclaw = openclaw
    app.state.turn_registry = turn_registry
    app.state.client_registry = client_registry
    app.state.session_registry = SessionRegistry()

    yield

    await openclaw.close()
    await db_client.close()


app = FastAPI(
    title="JotaGateway (BFF)",
    description="Backend For Frontend - Enrutador principal de WebSockets.",
    version="3.0.0",
    lifespan=lifespan,
)

# WebSocket
app.include_router(stream_router)

# OpenAI-compatible REST
app.include_router(openai_router)

# Health probes (added in Task 2 — placeholder for now)
app.include_router(health_router)

# Observability (moved to admin in Task 4)
app.include_router(orchestrator_router, prefix="/api")
app.include_router(sessions_router, prefix="/api")
```

- [ ] **Step 3: Update `src/api/deps.py` — remove `get_verified_client`, keep `handle_db_error`**

```python
"""
deps.py
~~~~~~~
FastAPI dependencies compartidas por los routers de la API.
"""
import httpx
from fastapi import HTTPException


def handle_db_error(e: Exception) -> None:
    if isinstance(e, httpx.HTTPStatusError):
        raise HTTPException(status_code=e.response.status_code)
    if isinstance(e, httpx.RequestError):
        raise HTTPException(status_code=503, detail="jota-db unavailable")
    raise HTTPException(status_code=502, detail="Unexpected error")
```

- [ ] **Step 4: Simplify `tests/integration/conftest.py` — remove dead mocks from `mock_services`**

Remove the config, conversations and models mock blocks (lines that mock `/config/me`, `/conversations`, `/models`). Keep auth, transcriber health, TTS health. Updated `mock_services` fixture body:

```python
@pytest.fixture
def mock_services():
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
        # --- jota-db: auth ---
        router.get(f"{_DB_BASE}/auth/session").mock(
            side_effect=lambda req: (
                httpx.Response(200, json=SESSION_RESPONSE)
                if req.headers.get("x-api-key") == VALID_KEY
                else httpx.Response(401, json={"detail": "Invalid key"})
            )
        )
        # --- transcriber: health ---
        router.get("http://localhost:9000/health").mock(
            return_value=httpx.Response(200)
        )
        # --- TTS: health ---
        router.get("http://localhost:8005/health").mock(
            return_value=httpx.Response(200)
        )
        yield router
```

Also remove the `CONFIG_RESPONSE` constant (no longer used):

```python
# DELETE this line:
CONFIG_RESPONSE = SESSION_RESPONSE["config"]
```

- [ ] **Step 5: Run tests — verify only unrelated failures remain**

```bash
PYTHONPATH=. pytest tests/ -v 2>&1 | head -60
```

Expected: tests for conversations/models/config no longer exist. Tests for remaining routes pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: remove dead jota-db proxy routes (conversations, models, config)"
```

---

## Task 2: Health endpoints — /healthz and /ready

**Files:**
- Modify: `src/api/health_routes.py`
- Modify: `src/main.py` — register health router at root (no prefix)
- Modify: `tests/integration/test_rest_health.py`

**Interfaces:**
- Produces: `GET /healthz` → always 200 `{"status":"ok"}`; `GET /ready` → 200 or 503 with service states

- [ ] **Step 1: Write failing tests first**

Replace the entire contents of `tests/integration/test_rest_health.py`:

```python
"""Tests for GET /healthz and GET /ready."""
import httpx
from unittest.mock import AsyncMock, patch


def test_healthz_always_200(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_all_ok(client):
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["services"]["orchestrator"] == "ok"
    assert body["services"]["transcriber"] == "ok"
    assert body["services"]["tts"] == "ok"


def test_ready_orchestrator_down_returns_503(client, mock_orchestrator):
    mock_orchestrator.ping = AsyncMock(return_value=False)
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "unavailable"
    assert r.json()["services"]["orchestrator"] == "unavailable"


def test_ready_tts_down_returns_200_degraded(client, mock_services):
    mock_services.get("http://localhost:8005/health").mock(
        side_effect=httpx.ConnectError("down")
    )
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["services"]["tts"] == "unavailable"
    assert body["services"]["orchestrator"] == "ok"


def test_ready_transcriber_down_returns_200_degraded(client, mock_services):
    mock_services.get("http://localhost:9000/health").mock(
        side_effect=httpx.ConnectError("down")
    )
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["services"]["transcriber"] == "unavailable"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
PYTHONPATH=. pytest tests/integration/test_rest_health.py -v
```

Expected: FAIL — `/healthz` returns 404, old `/api/health` tests are gone.

- [ ] **Step 3: Rewrite `src/api/health_routes.py`**

```python
"""
health_routes.py
~~~~~~~~~~~~~~~~
GET /healthz  — liveness: always 200 if the process is running
GET /ready    — readiness: pings OpenClaw (critical), TTS and transcriber (non-critical)

OpenClaw down → 503 "unavailable"
TTS or transcriber down → 200 "degraded"
All ok → 200 "ok"
"""
import asyncio
from fastapi import APIRouter, Request, Response

from src.core.config import settings
from src.services.transcriber_client import TranscriberClient
from src.services.tts_client import TTSClient

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


async def _ping_orchestrator(request: Request) -> str:
    try:
        ok = await request.app.state.openclaw.ping()
        return "ok" if ok else "unavailable"
    except Exception:
        return "unavailable"


async def _ping_transcriber() -> str:
    ok = await TranscriberClient.ping(settings.TRANSCRIBER_WS_URL)
    return "ok" if ok else "unavailable"


async def _ping_tts() -> str:
    ok = await TTSClient.ping(settings.TTS_WS_URL)
    return "ok" if ok else "unavailable"


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict:
    results = await asyncio.gather(
        _ping_orchestrator(request),
        _ping_transcriber(),
        _ping_tts(),
        return_exceptions=True,
    )

    def _resolve(r) -> str:
        return "unavailable" if isinstance(r, Exception) else r

    services = {
        "orchestrator": _resolve(results[0]),
        "transcriber": _resolve(results[1]),
        "tts": _resolve(results[2]),
    }

    if services["orchestrator"] == "unavailable":
        status = "unavailable"
        response.status_code = 503
    elif any(v == "unavailable" for v in services.values()):
        status = "degraded"
    else:
        status = "ok"

    return {"status": status, "services": services}
```

- [ ] **Step 4: Update `src/main.py` — register health router at root (no prefix)**

Change the health router registration line. Replace:
```python
app.include_router(health_router)
```
with (it's already there from Task 1, just confirm it has no prefix argument). The router itself defines `/healthz` and `/ready` at root level, so no prefix needed.

- [ ] **Step 5: Run tests — verify they pass**

```bash
PYTHONPATH=. pytest tests/integration/test_rest_health.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/api/health_routes.py src/main.py tests/integration/test_rest_health.py
git commit -m "feat: replace /api/health with /healthz (liveness) and /ready (readiness, 503 on orchestrator down)"
```

---

## Task 3: Admin infrastructure (config, deps, reconnecting, /v1/models)

**Files:**
- Modify: `src/core/config.py`
- Modify: `src/api/deps.py`
- Modify: `src/services/openclaw/reconnecting.py`
- Modify: `src/api/openai_routes.py`
- Modify: `tests/integration/conftest.py`

**Interfaces:**
- Produces: `get_admin_auth` dependency in `deps.py`; `openclaw.get_name()` method; `ADMIN_TOKEN` in settings

- [ ] **Step 1: Add `ADMIN_TOKEN` to `src/core/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    JOTA_DB_BASE_URL: str = "localhost:8001"
    JOTA_DB_API_KEY: str = ""
    TRANSCRIBER_WS_URL: str = "localhost:9000"
    TTS_WS_URL: str = "localhost:8005"
    TTS_TOKEN: str = "gateway"
    OPENCLAW_HOST: str = "127.0.0.1"
    OPENCLAW_PORT: int = 18789
    OPENCLAW_TOKEN: str = ""
    ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF: float = 1.0
    ORCHESTRATOR_RECONNECT_MAX_BACKOFF: float = 60.0
    ORCHESTRATOR_RECONNECT_MAX_DURATION: float = 300.0
    TRANSCRIBER_SILENCE_TIMEOUT_S: int = 25
    ADMIN_TOKEN: str = ""

settings = Settings()
```

- [ ] **Step 2: Add `get_admin_auth` to `src/api/deps.py`**

```python
"""
deps.py
~~~~~~~
FastAPI dependencies compartidas por los routers de la API.
"""
import httpx
from fastapi import Header, HTTPException

from src.core.config import settings


def handle_db_error(e: Exception) -> None:
    if isinstance(e, httpx.HTTPStatusError):
        raise HTTPException(status_code=e.response.status_code)
    if isinstance(e, httpx.RequestError):
        raise HTTPException(status_code=503, detail="jota-db unavailable")
    raise HTTPException(status_code=502, detail="Unexpected error")


async def get_admin_auth(x_admin_token: str = Header(...)) -> None:
    """Validates X-Admin-Token against ADMIN_TOKEN env var.

    Returns 503 if ADMIN_TOKEN is not configured (prevents accidental exposure).
    Returns 401 if token does not match.
    """
    if not settings.ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin API not configured")
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")
```

- [ ] **Step 3: Add `get_name()` to `src/services/openclaw/reconnecting.py`**

After the `__init__` method (around line 58), add:

```python
    def get_name(self) -> str:
        return self._name
```

- [ ] **Step 4: Fix `/v1/models` in `src/api/openai_routes.py`**

Change the `list_models` function to return a meaningful static entry:

```python
@router.get("/models")
async def list_models(request: Request):
    return JSONResponse({
        "object": "list",
        "data": [{"id": "jota-gateway", "object": "model", "created": 0, "owned_by": "jota-gateway"}],
    })
```

- [ ] **Step 5: Update `/v1/models` test in `tests/integration/test_rest_openai.py`**

Find the assertion for the model id and update it. Search for the existing test:

```bash
grep -n "openclaw\|model.*id\|id.*model" tests/integration/test_rest_openai.py
```

Update whichever line asserts the model id value:

```python
# Old:
assert data["data"][0]["id"] == "openclaw"
# New:
assert data["data"][0]["id"] == "jota-gateway"
```

- [ ] **Step 6: Add admin fixtures to `tests/integration/conftest.py`**

Add after the existing constants (after `CLIENT_ID = "hab_sito"`):

```python
ADMIN_TOKEN = "test-admin-token"
```

Add after the `clear_db_cache` fixture:

```python
@pytest.fixture(autouse=True)
def configure_admin_token():
    """Set ADMIN_TOKEN for all integration tests."""
    original = settings.ADMIN_TOKEN
    settings.ADMIN_TOKEN = ADMIN_TOKEN
    yield
    settings.ADMIN_TOKEN = original


@pytest.fixture
def admin_headers():
    return {"x-admin-token": ADMIN_TOKEN}
```

Also add `settings` to the conftest imports:

```python
from src.core.config import settings
```

- [ ] **Step 7: Write failing test for admin auth**

Create `tests/integration/test_admin_auth.py`:

```python
"""Tests for /admin/* authentication."""


def test_admin_missing_token_returns_422(client):
    """No X-Admin-Token header → 422 (missing required header)."""
    r = client.get("/admin/sessions")
    assert r.status_code == 422


def test_admin_wrong_token_returns_401(client):
    r = client.get("/admin/sessions", headers={"x-admin-token": "wrong"})
    assert r.status_code == 401


def test_admin_no_token_configured_returns_503(client, monkeypatch):
    from src.core.config import settings
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "")
    r = client.get("/admin/sessions", headers={"x-admin-token": "anything"})
    assert r.status_code == 503
```

(These will pass fully after Task 4 creates the admin router.)

- [ ] **Step 8: Run tests — verify no regressions**

```bash
PYTHONPATH=. pytest tests/ -v --ignore=tests/integration/test_admin_auth.py 2>&1 | tail -20
```

Expected: all previously passing tests still pass.

- [ ] **Step 9: Commit**

```bash
git add src/core/config.py src/api/deps.py src/services/openclaw/reconnecting.py src/api/openai_routes.py tests/integration/conftest.py tests/integration/test_rest_openai.py tests/integration/test_admin_auth.py
git commit -m "feat: add ADMIN_TOKEN, get_admin_auth dependency, get_name() on orchestrator, fix /v1/models id"
```

---

## Task 4: Admin routes — observability + CRUD stubs

**Files:**
- Create: `src/api/admin_routes.py`
- Delete: `src/api/orchestrator_routes.py`, `src/api/sessions_routes.py`
- Modify: `src/main.py`
- Modify: `tests/integration/test_orchestrator_routes.py`
- Modify: `tests/integration/test_sessions_api.py`

**Interfaces:**
- Consumes: `get_admin_auth` from `deps.py`; `openclaw.get_name()` from reconnecting.py
- Produces: `/admin/sessions`, `/admin/sessions/{id}`, `/admin/orchestrators/{name}/status`, `/admin/orchestrators/{name}/reconnect`, `/admin/clients` (501 stubs)

- [ ] **Step 1: Create `src/api/admin_routes.py`**

```python
"""
admin_routes.py
~~~~~~~~~~~~~~~
/admin/* — gestión y observabilidad del gateway.

Auth: X-Admin-Token header validado contra ADMIN_TOKEN env var (via get_admin_auth).

Clientes:
  GET    /admin/clients              — lista clientes (stub → 501 hasta DB session)
  POST   /admin/clients              — crear cliente (stub → 501)
  GET    /admin/clients/{id}         — detalle (stub → 501)
  PATCH  /admin/clients/{id}         — actualizar (stub → 501)
  DELETE /admin/clients/{id}         — borrar (stub → 501)
  POST   /admin/clients/{id}/rotate-key — rotar key (stub → 501)

Observabilidad:
  GET    /admin/sessions             — sesiones en memoria
  GET    /admin/sessions/{id}        — detalle de sesión
  GET    /admin/orchestrators/{name}/status
  POST   /admin/orchestrators/{name}/reconnect  (202)
"""
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.deps import get_admin_auth

router = APIRouter(prefix="/admin", dependencies=[Depends(get_admin_auth)])


# ---------------------------------------------------------------------------
# Client CRUD — stubs until DB session is implemented
# ---------------------------------------------------------------------------

@router.get("/clients")
async def list_clients() -> dict:
    raise HTTPException(status_code=501, detail="Not implemented — pending DB session")


@router.post("/clients", status_code=201)
async def create_client() -> dict:
    raise HTTPException(status_code=501, detail="Not implemented — pending DB session")


@router.get("/clients/{client_id}")
async def get_client(client_id: str) -> dict:
    raise HTTPException(status_code=501, detail="Not implemented — pending DB session")


@router.patch("/clients/{client_id}")
async def update_client(client_id: str) -> dict:
    raise HTTPException(status_code=501, detail="Not implemented — pending DB session")


@router.delete("/clients/{client_id}", status_code=204)
async def delete_client(client_id: str) -> None:
    raise HTTPException(status_code=501, detail="Not implemented — pending DB session")


@router.post("/clients/{client_id}/rotate-key")
async def rotate_client_key(client_id: str) -> dict:
    raise HTTPException(status_code=501, detail="Not implemented — pending DB session")


# ---------------------------------------------------------------------------
# Sessions — observabilidad en memoria
# ---------------------------------------------------------------------------

def _serialize_events(events) -> list[dict]:
    return [
        {
            "stage": e.stage,
            "ts_ms": round(e.ts_ms),
            "elapsed_ms": round(e.elapsed_ms),
            "meta": e.meta,
        }
        for e in events
    ]


def _session_summary(record) -> dict:
    t = record.tracker
    return {
        "session_id": record.session_id,
        "client_id": record.client_id,
        "status": record.status,
        "input_mode": record.input_mode,
        "output_mode": record.output_mode,
        "started_at": record.started_at.isoformat(),
        "ended_at": record.ended_at.isoformat() if record.ended_at else None,
        "turn_count": t.turn_count,
        "last_latencies": {
            "llm_first_token_ms": t.llm_first_token_ms(),
            "tts_first_chunk_ms": t.tts_first_chunk_ms(),
            "turn_e2e_ms": t.turn_e2e_ms(),
        },
    }


def _session_detail(record) -> dict:
    t = record.tracker
    events = t.events

    end_event = next((e for e in reversed(events) if e.stage == "session_end"), None)
    duration_s = end_event.meta.get("duration_s") if end_event else None

    by_turn: dict[int, dict] = defaultdict(dict)
    for e in events:
        by_turn[e.turn][e.stage] = e

    llm_latencies = [
        round(turn_events["llm_first_token"].ts_ms - turn_events["llm_start"].ts_ms, 1)
        for turn_events in by_turn.values()
        if "llm_start" in turn_events and "llm_first_token" in turn_events
    ]
    avg_llm_first_token_ms = round(sum(llm_latencies) / len(llm_latencies), 1) if llm_latencies else None

    e2e_latencies = [
        round(turn_events["tts_done"].ts_ms - turn_events["transcription_final"].ts_ms, 1)
        for turn_events in by_turn.values()
        if "transcription_final" in turn_events and "tts_done" in turn_events
    ]
    avg_turn_e2e_ms = round(sum(e2e_latencies) / len(e2e_latencies), 1) if e2e_latencies else None

    return {
        **_session_summary(record),
        "summary": {
            "turn_count": t.turn_count,
            "duration_s": duration_s,
            "avg_llm_first_token_ms": avg_llm_first_token_ms,
            "avg_turn_e2e_ms": avg_turn_e2e_ms,
        },
        "events": _serialize_events(events),
    }


@router.get("/sessions")
async def list_sessions(request: Request) -> dict:
    registry = request.app.state.session_registry
    sessions = registry.get_all()
    active = sum(1 for s in sessions if s.status == "active")
    return {
        "active": active,
        "total": len(sessions),
        "sessions": [_session_summary(s) for s in sessions],
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> dict:
    registry = request.app.state.session_registry
    record = registry.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_detail(record)


# ---------------------------------------------------------------------------
# Orchestrators — estado y control
# ---------------------------------------------------------------------------

@router.get("/orchestrators/{name}/status")
async def get_orchestrator_status(name: str, request: Request) -> dict:
    openclaw = request.app.state.openclaw
    if name != openclaw.get_name():
        raise HTTPException(status_code=404, detail=f"Orchestrator '{name}' not registered")
    s = openclaw.status()
    return {
        "name": s.name,
        "state": s.state.value,
        "connected_at": s.connected_at.isoformat() if s.connected_at else None,
        "disconnected_at": None,
        "reconnect_attempts": s.reconnect_attempts,
        "last_error": s.last_error,
    }


@router.post("/orchestrators/{name}/reconnect", status_code=202)
async def post_orchestrator_reconnect(name: str, request: Request) -> dict:
    openclaw = request.app.state.openclaw
    if name != openclaw.get_name():
        raise HTTPException(status_code=404, detail=f"Orchestrator '{name}' not registered")
    await openclaw.connect()
    return {"accepted": True}
```

- [ ] **Step 2: Update `src/main.py` — swap old routers for admin router**

```python
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.api.routes import router as stream_router
from src.api.health_routes import router as health_router
from src.api.openai_routes import router as openai_router
from src.api.admin_routes import router as admin_router
from src.core.config import settings
from src.services.db_client import db_client
from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.reconnecting import ReconnectingOpenClawClient
from src.services.openclaw.registry import TurnRegistry, ClientRegistry
from src.services.session_registry import SessionRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_client.connect()

    turn_registry = TurnRegistry()
    client_registry = ClientRegistry()
    dispatcher = FrameDispatcher(turn_registry, client_registry)
    inner = OpenClawClient(
        host=settings.OPENCLAW_HOST,
        port=settings.OPENCLAW_PORT,
        token=settings.OPENCLAW_TOKEN,
        turn_registry=turn_registry,
        dispatcher=dispatcher,
    )
    openclaw = ReconnectingOpenClawClient(
        inner,
        name="openclaw",
        initial_backoff=settings.ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF,
        max_backoff=settings.ORCHESTRATOR_RECONNECT_MAX_BACKOFF,
        max_duration=settings.ORCHESTRATOR_RECONNECT_MAX_DURATION,
    )
    try:
        await openclaw.connect()
    except Exception as e:
        logger.error(f"Initial OpenClaw connect failed: {e}")

    app.state.openclaw = openclaw
    app.state.turn_registry = turn_registry
    app.state.client_registry = client_registry
    app.state.session_registry = SessionRegistry()

    yield

    await openclaw.close()
    await db_client.close()


app = FastAPI(
    title="JotaGateway (BFF)",
    description="Backend For Frontend - Enrutador principal de WebSockets.",
    version="3.0.0",
    lifespan=lifespan,
)

app.include_router(stream_router)           # WS /ws/stream
app.include_router(openai_router)           # HTTP /v1/*
app.include_router(health_router)           # HTTP /healthz, /ready
app.include_router(admin_router)            # HTTP /admin/*
```

- [ ] **Step 3: Delete old route files**

```bash
rm src/api/orchestrator_routes.py src/api/sessions_routes.py
```

- [ ] **Step 4: Add `get_name` mock to conftest**

In `tests/integration/conftest.py`, in the `make_mock_orchestrator` function, add `get_name` mock after `mock._name = "openclaw"`:

```python
mock.get_name = MagicMock(return_value="openclaw")
```

- [ ] **Step 5: Update `tests/integration/test_orchestrator_routes.py`**

Replace entire file:

```python
"""Integration tests for /admin/orchestrators/{name}/status and /reconnect."""
from unittest.mock import AsyncMock


def test_get_orchestrator_status_connected(client, admin_headers):
    response = client.get("/admin/orchestrators/openclaw/status", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "openclaw"
    assert data["state"] == "CONNECTED"
    assert data["reconnect_attempts"] == 0
    assert data["last_error"] is None


def test_get_orchestrator_status_not_found(client, admin_headers):
    response = client.get("/admin/orchestrators/unknown/status", headers=admin_headers)
    assert response.status_code == 404


def test_get_orchestrator_status_requires_admin_token(client):
    response = client.get("/admin/orchestrators/openclaw/status")
    assert response.status_code == 422


def test_post_orchestrator_reconnect_accepted(client, admin_headers):
    response = client.post("/admin/orchestrators/openclaw/reconnect", headers=admin_headers)
    assert response.status_code == 202


def test_post_orchestrator_reconnect_not_found(client, admin_headers):
    response = client.post("/admin/orchestrators/unknown/reconnect", headers=admin_headers)
    assert response.status_code == 404
```

- [ ] **Step 6: Update `tests/integration/test_sessions_api.py`**

Replace all `/api/sessions` with `/admin/sessions` and all `auth_headers` with `admin_headers`. Also update auth test:

```python
from unittest.mock import AsyncMock, MagicMock
from src.services.pipeline_tracker import PipelineTracker


def _make_live_tracker(app, session_id="sess:111", output_mode=None):
    ws = AsyncMock()
    registry_mock = MagicMock()
    tracker = PipelineTracker(
        session_id=session_id,
        client_id="client-abc",
        input_mode="audio",
        output_mode=output_mode or ["audio", "text"],
        client_ws=ws,
        registry=registry_mock,
    )
    app.state.session_registry.register(tracker)
    return tracker


def test_list_sessions_empty(client, admin_headers):
    r = client.get("/admin/sessions", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["active"] == 0
    assert data["total"] == 0
    assert data["sessions"] == []


def test_list_sessions_requires_admin_token(client):
    r = client.get("/admin/sessions")
    assert r.status_code == 422


def test_list_sessions_shows_active_session(client, admin_headers):
    from src.main import app
    _make_live_tracker(app, "sess:active")
    try:
        r = client.get("/admin/sessions", headers=admin_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["active"] == 1
        session = data["sessions"][0]
        assert session["session_id"] == "sess:active"
        assert session["status"] == "active"
        assert session["input_mode"] == "audio"
    finally:
        app.state.session_registry.close("sess:active", "completed")


def test_list_sessions_includes_completed(client, admin_headers):
    from src.main import app
    _make_live_tracker(app, "sess:done")
    app.state.session_registry.close("sess:done", "completed")
    r = client.get("/admin/sessions", headers=admin_headers)
    data = r.json()
    ids = [s["session_id"] for s in data["sessions"]]
    assert "sess:done" in ids


def test_get_session_not_found(client, admin_headers):
    r = client.get("/admin/sessions/nope:999", headers=admin_headers)
    assert r.status_code == 404


def test_get_session_returns_required_fields(client, admin_headers):
    from src.main import app
    _make_live_tracker(app, "sess:fields")
    r = client.get("/admin/sessions/sess:fields", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == "sess:fields"
    assert isinstance(data["events"], list)
    assert "summary" in data
    assert "turn_count" in data["summary"]
    assert "avg_llm_first_token_ms" in data["summary"]
    assert "avg_turn_e2e_ms" in data["summary"]
    app.state.session_registry.close("sess:fields", "completed")


def test_get_session_last_latencies_fields_present(client, admin_headers):
    from src.main import app
    _make_live_tracker(app, "sess:lat")
    r = client.get("/admin/sessions/sess:lat", headers=admin_headers)
    data = r.json()
    lat = data["last_latencies"]
    assert "llm_first_token_ms" in lat
    assert "tts_first_chunk_ms" in lat
    assert "turn_e2e_ms" in lat
    app.state.session_registry.close("sess:lat", "completed")


async def test_get_session_avg_latencies_computed_correctly(client, admin_headers):
    import asyncio
    from src.main import app

    ws = AsyncMock()
    registry_mock = MagicMock()
    tracker = PipelineTracker(
        session_id="sess:avgs",
        client_id="c1",
        input_mode="audio",
        output_mode=["text"],
        client_ws=ws,
        registry=registry_mock,
    )
    tracker.start_turn()
    await tracker.record("transcription_final")
    await tracker.record("llm_start")
    await asyncio.sleep(0.05)
    await tracker.record("llm_first_token")
    await tracker.record("tts_done")

    app.state.session_registry.register(tracker)
    r = client.get("/admin/sessions/sess:avgs", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["avg_llm_first_token_ms"] is not None
    assert data["summary"]["avg_llm_first_token_ms"] >= 40
    assert data["summary"]["avg_turn_e2e_ms"] is not None
    assert data["summary"]["avg_turn_e2e_ms"] >= 40
    app.state.session_registry.close("sess:avgs", "completed")


async def test_get_session_avg_latencies_exclude_cancelled_turns(client, admin_headers):
    import asyncio
    from src.main import app

    ws = AsyncMock()
    registry_mock = MagicMock()
    tracker = PipelineTracker(
        session_id="sess:cancel",
        client_id="c1",
        input_mode="audio",
        output_mode=["text"],
        client_ws=ws,
        registry=registry_mock,
    )
    tracker.start_turn()
    await tracker.record("transcription_final")
    await tracker.record("llm_start")

    tracker.start_turn()
    await tracker.record("transcription_final")
    await tracker.record("llm_start")
    await asyncio.sleep(0.05)
    await tracker.record("llm_first_token")
    await tracker.record("tts_done")

    app.state.session_registry.register(tracker)
    r = client.get("/admin/sessions/sess:cancel", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["avg_llm_first_token_ms"] is not None
    assert data["summary"]["avg_llm_first_token_ms"] >= 40
    app.state.session_registry.close("sess:cancel", "completed")
```

- [ ] **Step 7: Run tests**

```bash
PYTHONPATH=. pytest tests/integration/test_orchestrator_routes.py tests/integration/test_sessions_api.py tests/integration/test_admin_auth.py -v
```

Expected: all pass.

- [ ] **Step 8: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -v 2>&1 | tail -20
```

Expected: no regressions.

- [ ] **Step 9: Commit**

```bash
git add src/api/admin_routes.py src/main.py tests/integration/test_orchestrator_routes.py tests/integration/test_sessions_api.py tests/integration/conftest.py
git commit -m "feat: /admin/* router — sessions, orchestrators observability + client CRUD stubs"
```

---

## Task 5: WebSocket — ready message

**Files:**
- Modify: `src/api/routes.py`
- Modify: `tests/integration/test_ws_handshake.py`
- Modify: `tests/integration/test_bridge_flow.py`

**Interfaces:**
- Produces: gateway sends `{"type":"ready","session_id":...,"agent":...,"input_mode":...,"output_mode":[...],"capabilities":{...}}` after `health_check()` returns True

- [ ] **Step 1: Write failing test — handshake now sends `ready`**

In `tests/integration/test_ws_handshake.py`, update `test_valid_text_mode_handshake_connection_stays_open`:

```python
def test_valid_text_mode_handshake_connection_stays_open(client):
    """Handshake válido — gateway responde con ready y la conexión permanece abierta."""
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({
            "client_key": VALID_KEY,
            "input_mode": "text",
            "output_mode": ["text"],
        })
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["input_mode"] == "text"
        assert ready["output_mode"] == ["text"]
        assert "session_id" in ready
        assert "agent" in ready
        assert "capabilities" in ready
        ws.send_text('{"type":"end"}')
```

- [ ] **Step 2: Run test — verify it fails**

```bash
PYTHONPATH=. pytest tests/integration/test_ws_handshake.py::test_valid_text_mode_handshake_connection_stays_open -v
```

Expected: FAIL — no `ready` message received, `receive_json()` blocks or raises.

- [ ] **Step 3: Update `src/api/routes.py` — send `ready` after health check**

After the `health_check()` success block (after the `if not await bridge.health_check()` early return), add before `bridge.run()`:

```python
    # Send ready — confirms session is established and announces capabilities
    resolved_agent = handshake.agent or default_agent
    try:
        await websocket.send_json({
            "type": "ready",
            "session_id": session_id,
            "agent": resolved_agent,
            "input_mode": handshake.input_mode,
            "output_mode": handshake.output_mode,
            "capabilities": {
                "barge_in": config.barge_in_enabled,
                "tts": "audio" in handshake.output_mode,
                "transcriber": handshake.input_mode == "audio",
            },
        })
    except Exception as e:
        logger.warning(f"[{client.id}] Failed to send ready: {e}")
        return
```

- [ ] **Step 4: Update `tests/integration/test_bridge_flow.py` — consume `ready` before messages**

In every test that opens a WS connection and sends text, add `ws.receive_json()` after `ws.send_json(HANDSHAKE_TEXT)` to consume the `ready` message. Do NOT yet add `turn_start` (that's Task 6).

`test_text_message_produces_token`:
```python
def test_text_message_produces_token(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.receive_json()  # ready
        ws.send_text("hola")
        msg = ws.receive_json()
        assert msg["type"] == "token"
        assert msg["content"] == "Hola"  # still "content" until Task 6
```

`test_orchestrator_receives_correct_user_id`:
```python
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.receive_json()  # ready
        ws.send_text("test")
        ws.receive_json()  # consumir token
```

`test_preferred_model_id_included_in_orchestrator_payload` and `test_system_prompt_extra_included_in_orchestrator_payload` — add `ws.receive_json()  # ready` after `ws.send_json(HANDSHAKE_TEXT)` in each.

- [ ] **Step 5: Run tests**

```bash
PYTHONPATH=. pytest tests/integration/test_ws_handshake.py tests/integration/test_bridge_flow.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -v 2>&1 | tail -20
```

- [ ] **Step 7: Commit**

```bash
git add src/api/routes.py tests/integration/test_ws_handshake.py tests/integration/test_bridge_flow.py
git commit -m "feat: send ready message after WS handshake health check"
```

---

## Task 6: WebSocket — typed turn lifecycle + status messages

**Files:**
- Modify: `src/services/bridge.py`
- Modify: `tests/unit/test_bridge_health_check.py`
- Modify: `tests/unit/test_bridge_barge_in.py`
- Modify: `tests/unit/test_bridge_push.py`
- Modify: `tests/integration/test_bridge_flow.py`

**Interfaces:**
- Produces: `turn_start {turn_id, turn_seq}` before tokens; `token {turn_id, text}` (not `content`); `turn_end {turn_id}` (replaces `done`); `error {code, message, fatal, turn_id?}`; `status {service, state}` (replaces `service_status`)

- [ ] **Step 1: Write failing unit tests**

Replace `tests/unit/test_bridge_health_check.py` — change `"status"` → `"state"` in payload assertions:

```python
"""Tests for JotaBridge.health_check() — four paths."""
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.pipeline_tracker import PipelineTracker, _NullWS

_CLIENT = Client(id="hab_sito", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


def _make_bridge(input_mode="text", output_mode=None):
    if output_mode is None:
        output_mode = ["text"]
    ws = AsyncMock()
    registry = MagicMock()
    tracker = PipelineTracker(
        session_id="test:hc", client_id="hab_sito",
        input_mode=input_mode, output_mode=output_mode,
        client_ws=_NullWS(), registry=registry,
    )
    handshake = Handshake(client_key="test-key", input_mode=input_mode, output_mode=output_mode)
    orch = AsyncMock()
    orch.ping = AsyncMock(return_value=True)
    bridge = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws,
                        orchestrator=orch, tracker=tracker, handshake=handshake,
                        client_registry=ClientRegistry(), default_agent="main")
    return bridge, ws, orch


async def test_health_check_returns_true_when_all_ok():
    bridge, ws, orch = _make_bridge()
    result = await bridge.health_check()
    assert result is True


async def test_health_check_returns_false_when_orchestrator_unavailable():
    bridge, ws, orch = _make_bridge()
    orch.ping = AsyncMock(return_value=False)
    result = await bridge.health_check()
    assert result is False
    ws.send_json.assert_called_once()
    payload = ws.send_json.call_args[0][0]
    assert payload["type"] == "status"
    assert payload["service"] == "orchestrator"
    assert payload["state"] == "unavailable"


async def test_health_check_returns_false_when_transcriber_not_ready():
    bridge, ws, orch = _make_bridge(input_mode="audio")
    bridge.transcriber = MagicMock()
    bridge.transcriber._is_ready = False
    result = await bridge.health_check()
    assert result is False
    payload = ws.send_json.call_args[0][0]
    assert payload["type"] == "status"
    assert payload["service"] == "transcriber"
    assert payload["state"] == "unavailable"


async def test_health_check_tts_unavailable_still_returns_true():
    bridge, ws, orch = _make_bridge(output_mode=["audio", "text"])
    with patch("src.services.tts_client.TTSClient.ping", new=AsyncMock(return_value=False)):
        result = await bridge.health_check()
    assert result is True
    payload = ws.send_json.call_args[0][0]
    assert payload["type"] == "status"
    assert payload["service"] == "tts"
    assert payload["state"] == "unavailable"
```

In `tests/unit/test_bridge_barge_in.py`, update `test_transcriber_warning_forwarded_to_client` and `test_transcriber_warning_uses_code_when_no_message`:

```python
async def test_transcriber_warning_forwarded_to_client(make_bridge):
    bridge = make_bridge()
    await bridge._on_transcriber_warning("buffer_full", "Buffer full")
    bridge.client_ws.send_json.assert_called_once_with({
        "type": "status",
        "service": "transcriber",
        "state": "degraded",
        "code": "buffer_full",
        "message": "Buffer full",
    })


async def test_transcriber_warning_uses_code_when_no_message(make_bridge):
    bridge = make_bridge()
    await bridge._on_transcriber_warning("timeout", None)
    payload = bridge.client_ws.send_json.call_args[0][0]
    assert payload["type"] == "status"
    assert payload["state"] == "degraded"
    assert payload["message"] == "timeout"
```

- [ ] **Step 2: Run unit tests — verify they fail**

```bash
PYTHONPATH=. pytest tests/unit/test_bridge_health_check.py tests/unit/test_bridge_barge_in.py -v 2>&1 | tail -20
```

Expected: failures on `"status"` vs `"state"` assertions.

- [ ] **Step 3: Update `src/services/bridge.py` — full typed protocol**

Add `_turn_seq: int = 0` to `__init__` (after `_session_start: float = 0.0`):

```python
        self._turn_seq: int = 0
```

Replace `health_check` — change all `service_status` → `status`, `status` key → `state`:

```python
    async def health_check(self) -> bool:
        if not await self.orchestrator.ping():
            await self.client_ws.send_json({
                "type": "status",
                "service": "orchestrator",
                "state": "unavailable",
            })
            return False

        if self.handshake.input_mode == "audio":
            if not self.transcriber or not self.transcriber._is_ready:
                await self.client_ws.send_json({
                    "type": "status",
                    "service": "transcriber",
                    "state": "unavailable",
                })
                return False

        if "audio" in self.handshake.output_mode:
            if not await TTSClient.ping(settings.TTS_WS_URL):
                await self.client_ws.send_json({
                    "type": "status",
                    "service": "tts",
                    "state": "unavailable",
                })

        return True
```

Replace `_on_transcriber_warning`:

```python
    async def _on_transcriber_warning(self, code: str, message: Optional[str]):
        try:
            await self.client_ws.send_json({
                "type": "status",
                "service": "transcriber",
                "state": "degraded",
                "code": code,
                "message": message or code,
            })
        except Exception:
            pass
```

In `run()`, replace the unexpected-transcriber-drop notification:

```python
            if self.transcriber and self.transcriber._dropped_unexpectedly and self._last_final_text is None:
                try:
                    await self.client_ws.send_json({
                        "type": "status",
                        "service": "transcriber",
                        "state": "unavailable",
                    })
                except Exception:
                    pass
```

In `_transcription_watchdog`, replace the degraded notification:

```python
                try:
                    await self.client_ws.send_json({
                        "type": "status",
                        "service": "transcriber",
                        "state": "degraded",
                    })
                except Exception:
                    pass
```

Replace `_call_orchestrator` entirely:

```python
    async def _call_orchestrator(self, text: str):
        from src.services.orchestration import call_orchestrator
        from src.core.session_key import make_session_key

        self._turn_seq += 1
        turn_seq = self._turn_seq
        turn_id = f"t-{turn_seq}"

        self.tracker.start_turn()

        try:
            await self.client_ws.send_json({"type": "turn_start", "turn_id": turn_id, "turn_seq": turn_seq})
        except Exception:
            return

        needs_audio = "audio" in self.handshake.output_mode

        tts: Optional[TTSClient] = None
        if needs_audio:
            tts = TTSClient(
                url=settings.TTS_WS_URL,
                token=settings.TTS_TOKEN,
                client_id=self.client_id,
            )
            try:
                await tts.connect(
                    voice=self.config.tts_voice,
                    speed=self.config.tts_speed,
                )
                await self.tracker.record("tts_start", voice=self.config.tts_voice or "")
            except Exception as e:
                logger.warning(f"[{self.client_id}] TTS no disponible, continuando en modo texto: {e}")
                tts = None

        agent = self.handshake.agent or self._default_agent
        session_key = make_session_key(agent, self.client_id)

        async def _on_token(token_text: str):
            try:
                if "text" in self.handshake.output_mode:
                    await self.client_ws.send_json({"type": "token", "turn_id": turn_id, "text": token_text})
            except Exception:
                pass
            if tts:
                await tts.send_text_chunk(token_text)

        async def pipe_tokens():
            try:
                await call_orchestrator(
                    self.orchestrator, text, session_key, self.client_id,
                    model_id=self.config.preferred_model_id,
                    system_prompt_extra=self.config.system_prompt_extra,
                    tracker=self.tracker,
                    on_token=_on_token,
                )
            except RuntimeError as e:
                try:
                    await self.client_ws.send_json({
                        "type": "error",
                        "code": "TURN_ERROR",
                        "message": str(e),
                        "fatal": False,
                        "turn_id": turn_id,
                    })
                except Exception:
                    pass
            finally:
                if tts:
                    await tts.end()

        async def pipe_audio():
            _first_chunk = True
            async for chunk in tts.get_audio_stream():
                if _first_chunk:
                    await self.tracker.record("tts_first_chunk")
                    _first_chunk = False
                try:
                    await self.client_ws.send_bytes(chunk)  # binary framing added in Task 7
                except Exception:
                    return
            await self.tracker.record("tts_done")

        if tts:
            try:
                await asyncio.gather(pipe_tokens(), pipe_audio())
            finally:
                await tts.close()
        else:
            await pipe_tokens()

        try:
            await self.client_ws.send_json({"type": "turn_end", "turn_id": turn_id})
        except Exception:
            pass
```

Add `_push_turn_seq` and `_push_turn_id` to `__init__`:

```python
        self._push_turn_seq: int = 0
        self._push_turn_id: Optional[str] = None
```

Replace `on_push_turn_start`:

```python
    async def on_push_turn_start(self, session_key: str) -> None:
        self._turn_seq += 1
        self._push_turn_seq = self._turn_seq
        self._push_turn_id = f"t-{self._turn_seq}"

        if "audio" not in self.handshake.output_mode:
            return
        tts = TTSClient(
            url=settings.TTS_WS_URL, token=settings.TTS_TOKEN, client_id=self.client_id
        )
        try:
            await tts.connect(voice=self.config.tts_voice, speed=self.config.tts_speed)
            self._push_tts = tts
        except Exception as e:
            logger.warning(f"[{self.client_id}] Push TTS unavailable: {e}")
            return

        async def _pipe_push_audio():
            async for chunk in tts.get_audio_stream():
                try:
                    await self.client_ws.send_bytes(chunk)  # binary framing added in Task 7
                except Exception:
                    return

        self._push_audio_task = asyncio.create_task(_pipe_push_audio())
```

Replace `deliver_push`:

```python
    async def deliver_push(self, payload: dict) -> None:
        delta = payload.get("deltaText", "")
        if not delta:
            return
        if "text" in self.handshake.output_mode:
            try:
                await self.client_ws.send_json({
                    "type": "token",
                    "turn_id": self._push_turn_id,
                    "text": delta,
                })
            except Exception:
                pass
        if self._push_tts:
            await self._push_tts.send_text_chunk(delta)
```

- [ ] **Step 4: Run unit tests — verify health_check and barge_in tests pass**

```bash
PYTHONPATH=. pytest tests/unit/test_bridge_health_check.py tests/unit/test_bridge_barge_in.py -v
```

Expected: all pass.

- [ ] **Step 5: Update `tests/unit/test_bridge_push.py` — new message formats**

Update `test_deliver_push_text_sends_push_message`:

```python
@pytest.mark.asyncio
async def test_deliver_push_text_sends_push_message():
    bridge, _ = make_bridge(output_mode=("text",))
    bridge._push_turn_id = "t-1"
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": "Buenos días!"}
    await bridge.deliver_push(payload)
    bridge.client_ws.send_json.assert_awaited_once_with({
        "type": "token",
        "turn_id": "t-1",
        "text": "Buenos días!",
    })
```

Update `test_push_audio_pipe_forwards_chunks_to_ws` — raw chunks still expected (binary framing in Task 7):

```python
@pytest.mark.asyncio
async def test_push_audio_pipe_forwards_chunks_to_ws():
    import asyncio
    bridge, _ = make_bridge(output_mode=("audio", "text"))
    with patch("src.services.bridge.TTSClient") as MockTTS:
        mock_tts = AsyncMock()
        chunks = [b"\x00\x01", b"\x02\x03"]
        mock_tts.get_audio_stream = lambda: aiter(chunks)
        MockTTS.return_value = mock_tts
        await bridge.on_push_turn_start("agent:main:hab_sito")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        calls = [c.args[0] for c in bridge.client_ws.send_bytes.await_args_list]
        assert calls == chunks  # raw until Task 7
```

Update `test_deliver_push_with_tts_sends_to_tts` — add `_push_turn_id`:

```python
@pytest.mark.asyncio
async def test_deliver_push_with_tts_sends_to_tts():
    bridge, _ = make_bridge(output_mode=("audio", "text"))
    bridge._push_turn_id = "t-1"
    mock_tts = AsyncMock()
    bridge._push_tts = mock_tts
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": "Hola!"}
    await bridge.deliver_push(payload)
    mock_tts.send_text_chunk.assert_awaited_once_with("Hola!")
```

- [ ] **Step 6: Update `tests/integration/test_bridge_flow.py` — add turn_start, update token format**

`test_text_message_produces_token`:

```python
def test_text_message_produces_token(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.receive_json()  # ready
        ws.send_text("hola")
        turn_start = ws.receive_json()
        assert turn_start["type"] == "turn_start"
        assert turn_start["turn_id"] == "t-1"
        assert turn_start["turn_seq"] == 1
        msg = ws.receive_json()
        assert msg["type"] == "token"
        assert msg["turn_id"] == "t-1"
        assert msg["text"] == "Hola"
```

`test_orchestrator_receives_correct_user_id`:

```python
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.receive_json()  # ready
        ws.send_text("test")
        ws.receive_json()  # turn_start
        ws.receive_json()  # token
```

`test_preferred_model_id_included_in_orchestrator_payload` and `test_system_prompt_extra_included_in_orchestrator_payload`: add `ws.receive_json()  # turn_start` after the existing `ws.receive_json()` (which is now the token).

Actually, in those tests the sequence is: `send_json(HANDSHAKE_TEXT)` → `receive_json() # ready (added Task 5)` → `send_text("test")` → now need `receive_json() # turn_start` → `receive_json() # token`. Update both to add the `turn_start` receive:

```python
        with c.websocket_connect("/ws/stream") as ws:
            ws.send_json(HANDSHAKE_TEXT)
            ws.receive_json()  # ready
            ws.send_text("test")
            ws.receive_json()  # turn_start
            ws.receive_json()  # token
```

- [ ] **Step 7: Run tests**

```bash
PYTHONPATH=. pytest tests/unit/test_bridge_health_check.py tests/unit/test_bridge_barge_in.py tests/unit/test_bridge_push.py tests/integration/test_bridge_flow.py -v
```

Expected: all pass.

- [ ] **Step 8: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -v 2>&1 | tail -30
```

- [ ] **Step 9: Commit**

```bash
git add src/services/bridge.py tests/unit/test_bridge_health_check.py tests/unit/test_bridge_barge_in.py tests/unit/test_bridge_push.py tests/integration/test_bridge_flow.py
git commit -m "feat: typed WS protocol — turn_start/end, token/text, error/code/fatal, status/state"
```

---

## Task 7: WebSocket — binary audio framing

**Files:**
- Modify: `src/services/bridge.py` — `pipe_audio` and `_pipe_push_audio`
- Modify: `tests/unit/test_bridge_push.py` — audio framing assertions
- Modify: `tests/integration/test_bridge_audio_flow.py` — new message sequence

**Interfaces:**
- Produces: binary audio frames from gateway = `[0xA1][turn_seq uint16 BE][PCM16 bytes]`

- [ ] **Step 1: Write failing unit test for audio framing**

Add to `tests/unit/test_bridge_push.py` — update the raw-chunks test to expect framed chunks:

```python
@pytest.mark.asyncio
async def test_push_audio_pipe_forwards_chunks_with_header():
    """Audio chunks from TTS include [0xA1][turn_seq uint16 BE] header."""
    import asyncio
    bridge, _ = make_bridge(output_mode=("audio", "text"))
    with patch("src.services.bridge.TTSClient") as MockTTS:
        mock_tts = AsyncMock()
        chunks = [b"\x00\x01", b"\x02\x03"]
        mock_tts.get_audio_stream = lambda: aiter(chunks)
        MockTTS.return_value = mock_tts
        await bridge.on_push_turn_start("agent:main:hab_sito")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        calls = [c.args[0] for c in bridge.client_ws.send_bytes.await_args_list]
        expected_header = bytes([0xA1]) + (1).to_bytes(2, "big")  # turn_seq=1
        assert calls == [expected_header + b"\x00\x01", expected_header + b"\x02\x03"]
```

- [ ] **Step 2: Run test — verify it fails**

```bash
PYTHONPATH=. pytest tests/unit/test_bridge_push.py::test_push_audio_pipe_forwards_chunks_with_header -v
```

Expected: FAIL — raw bytes without header.

- [ ] **Step 3: Update `src/services/bridge.py` — add header in `pipe_audio`**

Inside `_call_orchestrator`, replace the `pipe_audio` inner function with:

```python
        async def pipe_audio():
            header = bytes([0xA1]) + turn_seq.to_bytes(2, "big")
            _first_chunk = True
            async for chunk in tts.get_audio_stream():
                if _first_chunk:
                    await self.tracker.record("tts_first_chunk")
                    _first_chunk = False
                try:
                    await self.client_ws.send_bytes(header + chunk)
                except Exception:
                    return
            await self.tracker.record("tts_done")
```

Inside `on_push_turn_start`, replace the `_pipe_push_audio` inner function with:

```python
        async def _pipe_push_audio():
            header = bytes([0xA1]) + self._push_turn_seq.to_bytes(2, "big")
            async for chunk in tts.get_audio_stream():
                try:
                    await self.client_ws.send_bytes(header + chunk)
                except Exception:
                    return
```

- [ ] **Step 4: Run unit test — verify it passes**

```bash
PYTHONPATH=. pytest tests/unit/test_bridge_push.py::test_push_audio_pipe_forwards_chunks_with_header -v
```

Expected: PASS.

- [ ] **Step 5: Update the raw-chunks test — it should now also expect the header**

In `tests/unit/test_bridge_push.py`, remove `test_push_audio_pipe_forwards_chunks_to_ws` (now superseded by the header test). The new test `test_push_audio_pipe_forwards_chunks_with_header` covers the same scenario with the correct expectation.

- [ ] **Step 6: Check and update `tests/integration/test_bridge_audio_flow.py`**

Read the file and identify where it asserts on received messages. Look for any `receive_json()` calls that expect `{"type": "token", "content": ...}` or `{"type": "done"}`, and update:

```bash
grep -n "receive_json\|content\|done\|token\|turn" tests/integration/test_bridge_audio_flow.py
```

Update assertions following this mapping:
- `msg["content"]` → `msg["text"]`
- `{"type": "done"}` → `{"type": "turn_end", "turn_id": "t-1"}`
- Add `ws.receive_json()  # turn_start` before receiving first token
- Binary audio bytes received by client now have 3-byte header — if any test checks `len(chunk)` or specific byte values, account for the 3-byte prefix

- [ ] **Step 7: Run all bridge tests**

```bash
PYTHONPATH=. pytest tests/unit/test_bridge_push.py tests/integration/test_bridge_audio_flow.py tests/integration/test_bridge_flow.py -v
```

Expected: all pass.

- [ ] **Step 8: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -v 2>&1 | tail -30
```

Expected: all pass (or only pre-existing failures unrelated to this plan).

- [ ] **Step 9: Commit**

```bash
git add src/services/bridge.py tests/unit/test_bridge_push.py tests/integration/test_bridge_audio_flow.py
git commit -m "feat: binary audio frames include [0xA1][turn_seq uint16 BE] header for turn identification"
```

---

## Self-Review Checklist

After completing all tasks, verify:

- [ ] `PYTHONPATH=. pytest tests/ -v` — full suite passes
- [ ] `GET /healthz` → 200 `{"status":"ok"}` always
- [ ] `GET /ready` → 503 when OpenClaw is down, 200 degraded when TTS/transcriber down
- [ ] `GET /api/health` → 404 (old endpoint gone)
- [ ] `GET /admin/sessions` without token → 422; wrong token → 401
- [ ] `GET /admin/orchestrators/openclaw/status` with admin token → 200
- [ ] `GET /v1/models` → `[{"id": "jota-gateway", ...}]`
- [ ] WS handshake → receives `ready` as first message
- [ ] WS text send → receives `turn_start` then `token {text}` then `turn_end`
- [ ] No routes under `/api/*` prefix remain (except WS which was never under it)
- [ ] `ruff check src/ tests/` passes
