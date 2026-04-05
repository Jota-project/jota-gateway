# Fase 2 — REST API pública del Gateway — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exponer una capa REST HTTP en el gateway para que los clientes consulten y modifiquen su config, lean su historial y conozcan los modelos disponibles.

**Architecture:** Se añaden 4 routers FastAPI bajo prefijo `/api`, protegidos por la dependency `get_verified_client` que resuelve `X-API-Key` → `(Client, ClientConfig)` via jota-db. El `DbClient` singleton existente cubre config e historial; solo hay que añadir `get_models()`. También se normaliza la convención de URLs: los settings almacenan solo `host:port` y cada cliente inyecta el protocolo y path en su punto de uso.

**Tech Stack:** FastAPI, httpx, Pydantic v2, settings via pydantic-settings.

---

## File Map

| Estado | Fichero | Qué hace |
|---|---|---|
| Modify | `src/core/config.py` | Strip protocolos de los defaults |
| Modify | `src/services/db_client.py` | Inyectar `http://`, fix `get_messages()`, añadir `get_models()` |
| Modify | `src/services/orchestrator_client.py` | Inyectar `http://` en constructor |
| Modify | `src/services/transcriber_client.py` | Inyectar `ws://` + path en `connect()`; añadir `ping()` estático |
| Modify | `src/services/tts_client.py` | Inyectar `ws://` + `/ws` en `connect()`; adaptar `ping()` a `host:port` |
| Create | `src/api/deps.py` | `get_verified_client()` dependency |
| Create | `src/api/config_routes.py` | GET/PUT `/api/config`, POST `/api/config/reset` |
| Create | `src/api/conversation_routes.py` | GET `/api/conversations`, GET `/api/conversations/{id}/messages` |
| Create | `src/api/models_routes.py` | GET `/api/models` |
| Create | `src/api/health_routes.py` | GET `/api/health` (sin auth) |
| Modify | `src/main.py` | Montar los 4 nuevos routers bajo prefijo `/api` |
| Modify | `.env.sample` | Añadir `GATEWAY_KEY` |

---

## Task 1: Convención de URLs — strip protocolos de settings y clients

**Files:**
- Modify: `src/core/config.py`
- Modify: `src/services/db_client.py`
- Modify: `src/services/orchestrator_client.py`
- Modify: `src/services/transcriber_client.py`
- Modify: `src/services/tts_client.py`
- Modify: `.env.sample`

Los settings ahora almacenan solo `host[:port]`. Cada cliente inyecta el protocolo y path correcto.

- [ ] **Step 1: Actualizar `src/core/config.py`**

Reemplazar el contenido completo con:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # jota-db (fuente de verdad de identidad y configuración)
    # Solo host[:puerto] — el código inyecta http:// en DbClient
    JOTA_DB_BASE_URL: str = "localhost:8001"
    JOTA_DB_API_KEY: str = ""

    # URL base del JotaOrchestrator
    # Solo host[:puerto] — el código inyecta http:// en OrchestratorClient
    ORCHESTRATOR_BASE_URL: str = "localhost:8000"
    GATEWAY_KEY: str = ""

    # Transcriber (jota-transcriber)
    # Solo host[:puerto] — el código inyecta ws:// o http:// según el uso
    TRANSCRIBER_WS_URL: str = "localhost:9000"

    # TTS (jota-speaker)
    # Solo host[:puerto] — el código inyecta ws:// y path /ws en TTSClient
    TTS_WS_URL: str = "localhost:8005"
    TTS_TOKEN: str = "gateway"

    BARGE_IN_MIN_CHARS: int = 5
    TRANSCRIBER_SILENCE_TIMEOUT_S: int = 25

    class Config:
        env_file = ".env"

settings = Settings()
```

- [ ] **Step 2: Actualizar `src/services/db_client.py` — inyectar `http://` en constructor**

En el método `__init__`, cambiar la línea de `self.base_url`:

```python
def __init__(self, base_url: str, api_key: str):
    self.base_url = f"http://{base_url.rstrip('/')}"
    self._api_key = api_key
    self._http: Optional[httpx.AsyncClient] = None
```

- [ ] **Step 3: Actualizar `src/services/orchestrator_client.py` — inyectar `http://` en constructor**

En el método `__init__`, cambiar la línea de `self.base_url`:

```python
def __init__(self, base_url: str, api_key: str, client_id: str, timeout: float = 30.0):
    self.base_url = f"http://{base_url.rstrip('/')}"
    self.api_key = api_key
    self.client_id = client_id
    self.timeout = timeout
    self._http: Optional[httpx.AsyncClient] = None
```

- [ ] **Step 4: Actualizar `src/services/transcriber_client.py` — inyectar `ws://` y path `/api/stt` en `connect()`**

En `__init__`, guardar la URL raw (sin protocolo):

```python
def __init__(self, url: str, client_id: str):
    self.url = url  # host:port, sin protocolo
    self.client_id = client_id
    self.ws: Optional[websockets.WebSocketClientProtocol] = None
    self._is_ready = False
    self._session_id: Optional[str] = None
    self._dropped_unexpectedly: bool = False
    self._last_transcription_at: Optional[float] = None
```

En `connect()`, cambiar la línea de `websockets.connect`:

```python
self.ws = await websockets.connect(f"ws://{self.url}/api/stt")
```

También actualizar el log para mostrar la URL construida:

```python
logger.info(f"[{self.client_id}] Conectando a Transcriber (ws://{self.url}/api/stt) lang={language!r} vad={vad_thold}")
```

- [ ] **Step 5: Actualizar `src/services/tts_client.py` — inyectar `ws://` y path `/ws` en `connect()`; adaptar `ping()`**

En `connect()`, cambiar la línea de `websockets.connect` para inyectar protocolo y path:

```python
async def connect(self) -> None:
    """Open WS and authenticate. Raises RuntimeError on auth failure."""
    ws_url = f"ws://{self.url}/ws"
    self.ws = await websockets.connect(ws_url)
    await self.ws.send(json.dumps({"type": "auth", "token": self.token}))
    # ... resto igual
```

Actualizar también el log dentro de `connect()`:

```python
logger.info("[%s] Connected to TTS at ws://%s/ws", self.client_id, self.url)
```

Reemplazar el método estático `ping()` para aceptar `host:port` directamente:

```python
@staticmethod
async def ping(url: str) -> bool:
    """Return True if the TTS /health endpoint responds with 2xx.

    Expects url as host:port (no protocol, no path).
    Empty URLs return False.
    """
    if not url:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://{url}/health", timeout=5.0)
            return r.is_success
    except Exception:
        return False
```

- [ ] **Step 6: Añadir `GATEWAY_KEY` a `.env.sample`**

Añadir después de `ORCHESTRATOR_BASE_URL`:

```
# Clave que usa el gateway para autenticarse contra el orchestrator (e.g. health checks)
GATEWAY_KEY=gateway
```

- [ ] **Step 7: Commit**

```bash
git add src/core/config.py src/services/db_client.py src/services/orchestrator_client.py src/services/transcriber_client.py src/services/tts_client.py .env.sample
git commit -m "refactor: URL convention — strip protocols from settings, inject at point of use"
```

---

## Task 2: DbClient — fix `get_messages()` y añadir `get_models()`

**Files:**
- Modify: `src/services/db_client.py`

`get_messages()` actualmente no pasa `X-Client-Id` a jota-db, lo que impide que valide ownership. Se corrige añadiendo `client_id` al método. También se añade `get_models()`.

- [ ] **Step 1: Corregir `get_messages()` — añadir `client_id` y header `X-Client-Id`**

Localizar el método `get_messages` y reemplazarlo:

```python
async def get_messages(self, client_id: str, conversation_id: str) -> list:
    assert self._http
    r = await self._http.get(
        f"{self.base_url}/conversations/{conversation_id}/messages",
        headers={"X-Client-Id": client_id},
    )
    r.raise_for_status()
    return r.json()
```

- [ ] **Step 2: Añadir `get_models()` al final de la clase `DbClient`**

Añadir después de `get_messages`:

```python
# ------------------------------------------------------------------
# Modelos (usado en la REST API — Fase 2)
# ------------------------------------------------------------------

async def get_models(self) -> list:
    assert self._http
    r = await self._http.get(f"{self.base_url}/models")
    r.raise_for_status()
    return r.json()
```

- [ ] **Step 3: Commit**

```bash
git add src/services/db_client.py
git commit -m "fix: get_messages passes X-Client-Id for ownership validation; add get_models()"
```

---

## Task 3: TranscriberClient — añadir `ping()` estático

**Files:**
- Modify: `src/services/transcriber_client.py`

Necesario para que `health_routes.py` pueda hacer ping al transcriber via HTTP GET `/health`.

- [ ] **Step 1: Añadir `ping()` estático al final de la clase `TranscriberClient`**

Añadir antes del método `close()`:

```python
@staticmethod
async def ping(url: str) -> bool:
    """Return True if the transcriber HTTP /health responds with 2xx.

    Expects url as host:port (no protocol).
    """
    if not url:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://{url}/health", timeout=5.0)
            return r.is_success
    except Exception:
        return False
```

Añadir el import de `httpx` al principio del fichero (ya está `websockets`, hay que añadir `httpx`):

```python
import httpx
```

- [ ] **Step 2: Commit**

```bash
git add src/services/transcriber_client.py
git commit -m "feat: add TranscriberClient.ping() static method for health checks"
```

---

## Task 4: Crear `deps.py` — dependency `get_verified_client`

**Files:**
- Create: `src/api/deps.py`

Resuelve el header `X-API-Key` contra jota-db en cada request REST. Mapea errores de httpx a códigos HTTP correctos.

- [ ] **Step 1: Crear `src/api/deps.py`**

```python
"""
deps.py
~~~~~~~
FastAPI dependencies compartidas por todos los routers de la REST API.
"""
import httpx
from fastapi import Header, HTTPException

from src.models.schemas import Client, ClientConfig
from src.services.db_client import db_client


async def get_verified_client(
    x_api_key: str = Header(...),
) -> tuple[Client, ClientConfig]:
    """
    Resuelve X-API-Key → (Client, ClientConfig) llamando a jota-db.

    Raises:
        HTTPException 401: key inválida o cliente inactivo.
        HTTPException 503: jota-db no está disponible.
        HTTPException 502: error inesperado.
    """
    try:
        return await db_client.get_session(x_api_key)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            raise HTTPException(status_code=401, detail="Invalid or inactive API key")
        raise HTTPException(status_code=502, detail="Unexpected error from jota-db")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="jota-db unavailable")
    except Exception:
        raise HTTPException(status_code=502, detail="Unexpected error")
```

- [ ] **Step 2: Commit**

```bash
git add src/api/deps.py
git commit -m "feat: add get_verified_client dependency for REST API auth"
```

---

## Task 5: Crear `config_routes.py`

**Files:**
- Create: `src/api/config_routes.py`

Tres endpoints: GET devuelve config actual, PUT aplica patch parcial, POST /reset restaura defaults.

- [ ] **Step 1: Crear `src/api/config_routes.py`**

```python
"""
config_routes.py
~~~~~~~~~~~~~~~~
GET /api/config        — leer configuración del cliente
PUT /api/config        — actualizar campos (patch parcial)
POST /api/config/reset — restaurar defaults
"""
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException

from src.api.deps import get_verified_client
from src.models.schemas import Client, ClientConfig
from src.services.db_client import db_client

router = APIRouter()


def _handle_db_error(e: Exception) -> None:
    if isinstance(e, httpx.HTTPStatusError):
        raise HTTPException(status_code=e.response.status_code)
    if isinstance(e, httpx.RequestError):
        raise HTTPException(status_code=503, detail="jota-db unavailable")
    raise HTTPException(status_code=502, detail="Unexpected error")


@router.get("/config", response_model=ClientConfig)
async def get_config(
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> ClientConfig:
    client, _ = auth
    try:
        return await db_client.get_config(client.id)
    except Exception as e:
        _handle_db_error(e)


@router.put("/config", response_model=ClientConfig)
async def update_config(
    body: dict[str, Any] = Body(...),
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> ClientConfig:
    client, _ = auth
    try:
        return await db_client.update_config(client.id, body)
    except Exception as e:
        _handle_db_error(e)


@router.post("/config/reset", response_model=ClientConfig)
async def reset_config(
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> ClientConfig:
    client, _ = auth
    try:
        return await db_client.reset_config(client.id)
    except Exception as e:
        _handle_db_error(e)
```

- [ ] **Step 2: Commit**

```bash
git add src/api/config_routes.py
git commit -m "feat: add config_routes — GET/PUT /api/config, POST /api/config/reset"
```

---

## Task 6: Crear `conversation_routes.py`

**Files:**
- Create: `src/api/conversation_routes.py`

Lista conversaciones del cliente y sus mensajes. `get_messages` recibe `client_id` para que jota-db valide ownership.

- [ ] **Step 1: Crear `src/api/conversation_routes.py`**

```python
"""
conversation_routes.py
~~~~~~~~~~~~~~~~~~~~~~
GET /api/conversations                      — listar conversaciones
GET /api/conversations/{id}/messages        — mensajes de una conversación
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_verified_client
from src.models.schemas import Client, ClientConfig
from src.services.db_client import db_client

router = APIRouter()


def _handle_db_error(e: Exception) -> None:
    if isinstance(e, httpx.HTTPStatusError):
        raise HTTPException(status_code=e.response.status_code)
    if isinstance(e, httpx.RequestError):
        raise HTTPException(status_code=503, detail="jota-db unavailable")
    raise HTTPException(status_code=502, detail="Unexpected error")


@router.get("/conversations")
async def get_conversations(
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> list:
    client, _ = auth
    try:
        return await db_client.get_conversations(client.id)
    except Exception as e:
        _handle_db_error(e)


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> list:
    client, _ = auth
    try:
        return await db_client.get_messages(client.id, conversation_id)
    except Exception as e:
        _handle_db_error(e)
```

- [ ] **Step 2: Commit**

```bash
git add src/api/conversation_routes.py
git commit -m "feat: add conversation_routes — GET /api/conversations and /messages"
```

---

## Task 7: Crear `models_routes.py`

**Files:**
- Create: `src/api/models_routes.py`

Proxy directo a `GET /models` en jota-db. Requiere auth (misma dependency).

- [ ] **Step 1: Crear `src/api/models_routes.py`**

```python
"""
models_routes.py
~~~~~~~~~~~~~~~~
GET /api/models — lista de modelos disponibles (proxy a jota-db)
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_verified_client
from src.models.schemas import Client, ClientConfig
from src.services.db_client import db_client

router = APIRouter()


@router.get("/models")
async def get_models(
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> list:
    try:
        return await db_client.get_models()
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="jota-db unavailable")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code)
    except Exception:
        raise HTTPException(status_code=502, detail="Unexpected error")
```

- [ ] **Step 2: Commit**

```bash
git add src/api/models_routes.py
git commit -m "feat: add models_routes — GET /api/models"
```

---

## Task 8: Crear `health_routes.py`

**Files:**
- Create: `src/api/health_routes.py`

Sin auth. Pings paralelos a los tres servicios internos. Siempre devuelve `200` con el estado de cada servicio.

- [ ] **Step 1: Crear `src/api/health_routes.py`**

```python
"""
health_routes.py
~~~~~~~~~~~~~~~~
GET /api/health — estado de los servicios internos (sin auth, uso de operador)

Siempre devuelve 200. Los valores por servicio son "ok" o "unavailable".
"""
import asyncio
from fastapi import APIRouter

from src.core.config import settings
from src.services.orchestrator_client import OrchestratorClient
from src.services.transcriber_client import TranscriberClient
from src.services.tts_client import TTSClient

router = APIRouter()


async def _ping_orchestrator() -> str:
    client = OrchestratorClient(
        base_url=settings.ORCHESTRATOR_BASE_URL,
        api_key=settings.GATEWAY_KEY,
        client_id="gateway",
    )
    await client.connect()
    try:
        ok = await client.ping()
        return "ok" if ok else "unavailable"
    finally:
        await client.close()


async def _ping_transcriber() -> str:
    ok = await TranscriberClient.ping(settings.TRANSCRIBER_WS_URL)
    return "ok" if ok else "unavailable"


async def _ping_tts() -> str:
    ok = await TTSClient.ping(settings.TTS_WS_URL)
    return "ok" if ok else "unavailable"


@router.get("/health")
async def health() -> dict:
    orchestrator_status, transcriber_status, tts_status = await asyncio.gather(
        _ping_orchestrator(),
        _ping_transcriber(),
        _ping_tts(),
        return_exceptions=False,
    )
    return {
        "orchestrator": orchestrator_status,
        "transcriber": transcriber_status,
        "tts": tts_status,
    }
```

Nota: `return_exceptions=False` — si algún ping lanza una excepción no capturada, la propagará. Los métodos `ping()` ya capturan todas las excepciones internamente y devuelven `bool`, y `_ping_orchestrator` usa try/finally. El único riesgo es si `connect()` lanza en `_ping_orchestrator`. Para protegerlo, envolver el gather con un try/except general:

```python
@router.get("/health")
async def health() -> dict:
    results = await asyncio.gather(
        _ping_orchestrator(),
        _ping_transcriber(),
        _ping_tts(),
        return_exceptions=True,
    )

    def _resolve(r) -> str:
        if isinstance(r, Exception):
            return "unavailable"
        return r

    return {
        "orchestrator": _resolve(results[0]),
        "transcriber": _resolve(results[1]),
        "tts": _resolve(results[2]),
    }
```

Usar esta segunda versión (con `return_exceptions=True`).

- [ ] **Step 2: Commit**

```bash
git add src/api/health_routes.py
git commit -m "feat: add health_routes — GET /api/health with parallel service pings"
```

---

## Task 9: Actualizar `main.py` — montar los nuevos routers

**Files:**
- Modify: `src/main.py`

Montar los 4 nuevos routers bajo el prefijo `/api`. El router de WS no tiene prefijo (mantener como está).

- [ ] **Step 1: Actualizar `src/main.py`**

Reemplazar el contenido completo con:

```python
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.api.routes import router as stream_router
from src.api.config_routes import router as config_router
from src.api.conversation_routes import router as conversation_router
from src.api.models_routes import router as models_router
from src.api.health_routes import router as health_router
from src.services.db_client import db_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_client.connect()
    yield
    await db_client.close()


app = FastAPI(
    title="JotaGateway (BFF)",
    description="Backend For Frontend - Enrutador principal de WebSockets. Titiritero del Ecosistema IA.",
    version="2.0.0",
    lifespan=lifespan,
)

# WebSocket
app.include_router(stream_router)

# REST API
app.include_router(config_router, prefix="/api")
app.include_router(conversation_router, prefix="/api")
app.include_router(models_router, prefix="/api")
app.include_router(health_router, prefix="/api")


@app.get("/health")
def healthcheck():
    """Endpoint simple para probar que el Gateway está arriba"""
    return {
        "status": "online",
        "service": "JotaGateway BFF"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
```

- [ ] **Step 2: Commit**

```bash
git add src/main.py
git commit -m "feat: mount REST API routers — config, conversations, models, health (Fase 2, closes #1)"
```

---

## Self-Review

### Spec coverage

| Requisito | Task |
|---|---|
| URL convention cleanup (strip protocols) | Task 1 |
| `get_verified_client` dependency con 401/503 | Task 4 |
| GET/PUT/POST /api/config | Task 5 |
| GET /api/conversations + /messages | Task 6 |
| `get_messages` añade `X-Client-Id` | Task 2 |
| GET /api/models + `get_models()` | Task 2 + Task 7 |
| GET /api/health (sin auth, parallel pings) | Task 8 |
| `TranscriberClient.ping()` estático | Task 3 |
| Montar routers en main.py | Task 9 |
| GATEWAY_KEY en settings y .env.sample | Task 1 |
| Error mapping (401/503/502) | Task 4 + Tasks 5-7 |

Todos los requisitos están cubiertos.

### Placeholder scan

Ningún "TBD", "TODO", o descripción sin código encontrada.

### Type consistency

- `get_messages(client_id: str, conversation_id: str)` definido en Task 2, usado con `(client.id, conversation_id)` en Task 6. ✓
- `get_models()` definido en Task 2, llamado en Task 7. ✓
- `TranscriberClient.ping(url: str)` definido en Task 3, llamado en Task 8 con `settings.TRANSCRIBER_WS_URL`. ✓
- `TTSClient.ping(url: str)` adaptado en Task 1, llamado en Task 8 con `settings.TTS_WS_URL`. ✓
- `OrchestratorClient` recibe `base_url` sin protocolo desde Task 1, inyecta `http://` en constructor. ✓
