# Documentation Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar archivos de documentación obsoletos (specs/plans de features ya implementadas, artefactos internos de subagentes) y actualizar README.md y docs/client-protocol.md para que reflejen el estado real del codebase en v1.6.4.

**Architecture:** Trabajo puramente documental — sin cambios de código. Cada tarea produce un commit independiente. La verificación es siempre contra el código fuente real (grep/read), no hay tests automatizados.

**Tech Stack:** Markdown, git.

---

## Contexto previo (léelo antes de empezar)

El proyecto es `jota-gateway` (FastAPI BFF). El estado actual del código es **v1.6.4**. Los problemas documentados son:

1. **README.md** está casi completamente desactualizado — URL del WebSocket errónea, tabla de endpoints incompleta, tabla de configuración con variables que ya no existen, arquitectura interna que menciona clientes eliminados.
2. **`docs/client-protocol.md`** describe el protocolo WebSocket con un error crítico (el Handshake no documenta `client_key`, que es obligatorio) y la URL de conexión incorrecta.
3. **`docs/superpowers/`** contiene 6 specs/plans de features ya implementadas — son artefactos de desarrollo, no documentación viva.
4. **`.superpowers/sdd/`** contiene ~32 artefactos internos (task briefs, reports, diffs de code review) que no pertenecen al repo.
5. **`.github/TASKS.md`** era un doc de estado interno, desactualizado desde v1.6.0. La información que contiene está cubierta por CHANGELOG.md, CLAUDE.md y GitHub.

---

## File Map

| Acción | Ruta |
|---|---|
| Eliminar | `docs/superpowers/plans/2026-06-04-orchestrator-reconnect.md` |
| Eliminar | `docs/superpowers/plans/2026-06-09-audio-pipeline-monitoring.md` |
| Eliminar | `docs/superpowers/plans/2026-06-21-ws-http-coherence.md` |
| Eliminar | `docs/superpowers/specs/2026-06-04-orchestrator-reconnect-design.md` |
| Eliminar | `docs/superpowers/specs/2026-06-09-audio-pipeline-monitoring-design.md` |
| Eliminar | `docs/superpowers/specs/2026-06-21-ws-http-coherence-design.md` |
| Eliminar | `.superpowers/sdd/` (directorio completo — 32 archivos) |
| Eliminar | `.github/TASKS.md` |
| Reescribir | `README.md` |
| Actualizar | `docs/client-protocol.md` |

---

## Task 1: Eliminar artefactos obsoletos

Eliminar todos los archivos de specs, plans e implementación interna que ya no tienen valor. Todos son artefactos del proceso de desarrollo de features que están en producción desde v1.6.0.

**Files:**
- Delete: `docs/superpowers/plans/` (3 archivos)
- Delete: `docs/superpowers/specs/` (3 archivos)
- Delete: `.superpowers/sdd/` (directorio completo)
- Delete: `.github/TASKS.md`

- [ ] **Step 1: Eliminar los 6 archivos de specs y plans**

  ```bash
  git rm docs/superpowers/plans/2026-06-04-orchestrator-reconnect.md \
         docs/superpowers/plans/2026-06-09-audio-pipeline-monitoring.md \
         docs/superpowers/plans/2026-06-21-ws-http-coherence.md \
         docs/superpowers/specs/2026-06-04-orchestrator-reconnect-design.md \
         docs/superpowers/specs/2026-06-09-audio-pipeline-monitoring-design.md \
         docs/superpowers/specs/2026-06-21-ws-http-coherence-design.md
  ```

  **IMPORTANTE:** No elimines `docs/superpowers/plans/2026-06-29-docs-cleanup.md` — es este mismo plan.

- [ ] **Step 2: Eliminar el directorio `.superpowers/sdd/`**

  ```bash
  git rm -r .superpowers/sdd/
  ```

  Si el directorio no está rastreado por git (aparece como `??` en `git status`), usa:

  ```bash
  rm -rf .superpowers/sdd/
  ```

- [ ] **Step 3: Eliminar `.github/TASKS.md`**

  ```bash
  git rm .github/TASKS.md
  ```

- [ ] **Step 4: Verificar que no quedan archivos innecesarios**

  ```bash
  git status
  ```

  Espera ver solo los archivos eliminados en el staging area y este plan (`docs/superpowers/plans/2026-06-29-docs-cleanup.md`) como untracked o modified.

- [ ] **Step 5: Commit**

  ```bash
  git commit -m "chore(docs): remove obsolete specs, plans, and internal sdd artifacts"
  ```

---

## Task 2: Reescribir README.md

El README actual tiene la URL del WebSocket incorrecta, variables de entorno que no existen, endpoints faltantes y arquitectura desactualizada (menciona `JotaOrchestrator` HTTP NDJSON, que fue reemplazado por OpenClaw WebSocket v4).

**Files:**
- Rewrite: `README.md`

- [ ] **Step 1: Verificar el estado actual del código antes de escribir**

  Confirmar la URL real del WebSocket (debe ser `/ws/stream`, sin `{client_id}`):
  ```bash
  grep -n "websocket\|@router" src/api/routes.py
  ```
  Espera: `@router.websocket("/ws/stream")`

  Confirmar todos los campos de Settings:
  ```bash
  grep -A 20 "class Settings" src/core/config.py
  ```

  Confirmar todos los routers montados:
  ```bash
  grep "include_router" src/main.py
  ```

- [ ] **Step 2: Reemplazar README.md completamente**

  Escribe el siguiente contenido exacto en `README.md`:

  ````markdown
  # jota-gateway

  BFF (Backend For Frontend) del ecosistema Jota IA. Punto de entrada único para todos los clientes (ESP32, web, app, Home Assistant).

  ```
  ESP32 / Web / App / Home Assistant
          │
          ▼
    jota-gateway (FastAPI :8004)
    ├── /ws/stream          WebSocket — sesión interactiva de voz/texto
    ├── /api/*              REST API — config, historial, modelos, observabilidad
    └── /v1/*               OpenAI-compatible — integración Home Assistant
          │
          ▼
    ┌─────────────────────────────────────────┐
    │            Microservicios               │
    │  OpenClaw         WebSocket v4          │
    │  jota-transcriber WebSocket             │
    │  jota-tts         WebSocket             │
    │  jota-db          HTTP REST             │
    └─────────────────────────────────────────┘
  ```

  ---

  ## Endpoints

  | Endpoint | Método | Auth | Descripción |
  |---|---|---|---|
  | `/ws/stream` | WebSocket | `client_key` en handshake | Sesión interactiva de voz/texto |
  | `/api/health` | GET | X-API-Key | Estado de todos los microservicios |
  | `/api/models` | GET | X-API-Key | Modelos disponibles |
  | `/api/config` | GET | X-API-Key | Config del cliente autenticado |
  | `/api/config` | PUT | X-API-Key | Actualizar config |
  | `/api/config/reset` | POST | X-API-Key | Reset a valores por defecto |
  | `/api/conversations` | GET | X-API-Key | Historial de conversaciones |
  | `/api/conversations/{id}/messages` | GET | X-API-Key | Mensajes de una conversación |
  | `/api/conversations/{id}` | DELETE | X-API-Key | Archivar conversación |
  | `/api/orchestrators/{name}/status` | GET | X-API-Key | Estado de conexión del orquestador |
  | `/api/orchestrators/{name}/reconnect` | POST | X-API-Key | Forzar reconexión del orquestador |
  | `/api/sessions` | GET | X-API-Key | Sesiones activas y recientes |
  | `/api/sessions/{id}` | GET | X-API-Key | Detalle de una sesión |
  | `/v1/models` | GET | ninguna | Lista de modelos (OpenAI-compatible) |
  | `/v1/chat/completions` | POST | ninguna | Chat completion (OpenAI-compatible) |

  Los endpoints `/api/*` requieren cabecera `X-API-Key`. Los `/v1/*` no tienen autenticación — expuestos solo en LAN via nginx.

  ---

  ## Quick start

  ```bash
  pip install -r requirements.txt
  uvicorn src.main:app --host 0.0.0.0 --port 8004 --reload
  # o con Docker:
  docker compose up
  ```

  ---

  ## Configuración

  Variables en `.env`:

  | Variable | Default | Descripción |
  |---|---|---|
  | `JOTA_DB_BASE_URL` | `localhost:8001` | jota-db (`host:port`, sin protocolo) |
  | `JOTA_DB_API_KEY` | — | API key para jota-db |
  | `TRANSCRIBER_WS_URL` | `localhost:9000` | jota-transcriber (`host:port`) |
  | `TTS_WS_URL` | `localhost:8005` | jota-tts (`host:port`) |
  | `TTS_TOKEN` | `gateway` | Token de auth para jota-tts |
  | `OPENCLAW_HOST` | `127.0.0.1` | Host de OpenClaw |
  | `OPENCLAW_PORT` | `18789` | Puerto de OpenClaw |
  | `OPENCLAW_TOKEN` | — | Token de auth para OpenClaw (obligatorio) |
  | `OPENCLAW_DEFAULT_AGENT` | `main` | Agente OpenClaw por defecto |
  | `DEFAULT_ORCHESTRATOR` | `openclaw` | Orquestador activo en el registry |
  | `ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial en reconexión (s) |
  | `ORCHESTRATOR_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo en reconexión (s) |
  | `ORCHESTRATOR_RECONNECT_MAX_DURATION` | `300.0` | Tiempo hasta entrar en DEGRADED (s) |
  | `TRANSCRIBER_SILENCE_TIMEOUT_S` | `25` | Silencio máximo antes de cerrar sesión de audio (s) |

  > Todos los campos `*_URL` son `host:port` sin protocolo. El protocolo (`ws://`, `http://`) se inyecta dentro de cada cliente de servicio.

  ---

  ## Protocolo de cliente WebSocket

  Ver [`docs/client-protocol.md`](docs/client-protocol.md) para la guía completa de integración.

  ---

  ## Arquitectura interna

  El gateway instancia un `JotaBridge` por cada sesión WebSocket. El orquestador (`OpenClawClient`) es un singleton persistente gestionado en el lifespan de la app, envuelto en `ReconnectingOrchestrator` (reconexión automática con backoff exponencial; estados CONNECTED / RECONNECTING / DEGRADED).

  ```
  src/
  ├── api/
  │   ├── routes.py                    WebSocket /ws/stream
  │   ├── openai_routes.py             /v1/chat/completions, /v1/models
  │   ├── orchestrator_routes.py       /api/orchestrators/*
  │   ├── sessions_routes.py           /api/sessions
  │   ├── config_routes.py             /api/config
  │   ├── conversation_routes.py       /api/conversations
  │   ├── health_routes.py             /api/health
  │   ├── models_routes.py             /api/models
  │   └── deps.py                      dependencia de autenticación (X-API-Key)
  ├── core/
  │   ├── config.py                    Settings (pydantic-settings, .env)
  │   ├── cache.py                     make_cache() — TTLCache + asyncio.Lock
  │   └── session_key.py               make_session_key() — formato canónico
  └── services/
      ├── bridge.py                    JotaBridge — coordinador de sesión WS
      ├── db_client.py                 HTTP → jota-db (singleton)
      ├── transcriber_client.py        WebSocket → jota-transcriber (por sesión)
      ├── tts_client.py                WebSocket → jota-tts (creado por turno)
      ├── orchestration.py             call_orchestrator() — helper compartido WS+HTTP
      ├── pipeline_tracker.py          PipelineTracker — latencias por turno
      ├── session_registry.py          SessionRegistry — observabilidad de sesiones
      └── orchestrators/
          ├── protocol.py              OrchestratorProtocol, OrchestratorEvent
          ├── openclaw_client.py       OpenClaw WebSocket v4
          ├── reconnecting.py          ReconnectingOrchestrator (state machine)
          └── registry.py             OrchestratorRegistry + build_registry()
  ```
  ````

- [ ] **Step 3: Verificar que el README es preciso contra el código real**

  Comprobar que el árbol de archivos en el README refleja lo que existe:
  ```bash
  ls src/api/ src/core/ src/services/ src/services/orchestrators/
  ```

  Comprobar que todas las variables de la tabla de configuración existen en Settings:
  ```bash
  grep -E "^\s+[A-Z_]+:" src/core/config.py
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add README.md
  git commit -m "docs: rewrite README — correct WS URL, endpoints, config vars, architecture"
  ```

---

## Task 3: Actualizar docs/client-protocol.md

El protocolo WebSocket en sí es mayormente correcto. Hay tres errores concretos:

1. **URL incorrecta** — dice `/ws/stream/<client_id>`, el endpoint real es `/ws/stream` (sin path param; la identidad va en el Handshake)
2. **Handshake incompleto** — falta `client_key` (campo obligatorio para autenticación) y `agent` (campo opcional para seleccionar el agente OpenClaw)
3. **Sección 2 ambigua** — describe `{"text":"..."}` como el formato canónico de envío de texto, pero desde v1.6.0 el flujo principal es review & send con `{"type":"send","text":"..."}`. El formato `{"text":"..."}` sigue funcionando como atajo de texto plano, pero no es el flujo documentado en la sección de audio

**Files:**
- Modify: `docs/client-protocol.md`

- [ ] **Step 1: Verificar la firma del Handshake contra el código**

  ```bash
  grep -A 10 "class Handshake" src/models/schemas.py
  ```

  Espera ver campos: `client_key: str`, `input_mode`, `output_mode`, `agent: Optional[str]`.

  Confirmar que `client_key` va en el JSON del handshake (no en la URL):
  ```bash
  grep -n "client_key\|handshake\|Handshake" src/api/routes.py | head -10
  ```

- [ ] **Step 2: Reemplazar la sección 1 — Conexión y handshake**

  Localiza el bloque que empieza con `**Endpoint:** \`ws://<host>:8004/ws/stream/<client_id>\`` y termina antes de `### Ejemplos de handshake por caso de uso`.

  Reemplázalo con:

  ```markdown
  **Endpoint:** `ws://<host>:8004/ws/stream`

  **El primer mensaje que envíes DEBE ser el handshake** — un JSON que declara tu identidad y los modos que usará este cliente:

  ```json
  {
    "client_key": "tu-api-key",
    "input_mode": "audio",
    "output_mode": ["audio", "text", "status"],
    "agent": "main"
  }
  ```

  | Campo | Tipo | Obligatorio | Descripción |
  |---|---|---|---|
  | `client_key` | string | ✓ | API key del cliente — validada contra jota-db |
  | `input_mode` | string | ✓ | `"text"` o `"audio"` |
  | `output_mode` | array | ✓ | Qué quiere recibir: `"text"`, `"audio"`, `"status"` |
  | `agent` | string | — | Agente OpenClaw a usar; por defecto el configurado en el gateway |

  Si `client_key` no es válida o el cliente está inactivo, el servidor cierra con código **1008**.
  ```

- [ ] **Step 3: Reemplazar la sección 2 — Enviar texto**

  Localiza el encabezado `## 2. Enviar texto` y su contenido hasta el separador `---` siguiente.

  Reemplázalo con:

  ```markdown
  ## 2. Enviar texto

  Hay dos formas de enviar texto al orquestador:

  ### Flujo review & send (canónico)

  Este es el flujo principal, tanto en modo audio como en modo texto con revisión. El cliente envía un mensaje con `type: "send"`:

  ```json
  {"type": "send", "text": "¿Cuál es la capital de Francia?"}
  ```

  Este es el mismo mensaje que se usa tras recibir una transcripción de audio (ver sección 3). El campo `text` puede ser diferente al transcrito original si el usuario lo editó.

  ### Texto directo (atajo)

  En modo texto puro, también puedes enviar el prompt directamente como un JSON con campo `text` (sin `type`):

  ```json
  {"text": "¿Cuál es la capital de Francia?"}
  ```

  Opcionalmente puedes especificar un modelo concreto:

  ```json
  {"text": "Explícame la relatividad", "model_id": "gpt-4o"}
  ```

  Si `text` está vacío o el JSON es inválido, recibirás un `error` y la conexión continúa.
  ```

- [ ] **Step 4: Actualizar la tabla de referencia — sección 8**

  Localiza la tabla `### Cliente → Gateway` en la sección 8.

  La fila del Handshake actualmente dice:
  ```
  | Handshake | `{"input_mode":"...", "output_mode":[...]}` | Primer mensaje, obligatorio |
  ```

  Reemplaza esa fila con:
  ```
  | Handshake | `{"client_key":"...", "input_mode":"...", "output_mode":[...], "agent":"..."}` | Primer mensaje, obligatorio — `agent` es opcional |
  ```

  La fila "Texto directo" actualmente dice:
  ```
  | Texto directo | `{"text":"...", "model_id":"..."}` | `input_mode="text"` — prompt directo, `model_id` opcional |
  ```

  Reemplaza esa fila con:
  ```
  | Texto directo | `{"text":"...", "model_id":"..."}` | Atajo de texto plano — alternativa a `{"type":"send","text":"..."}` |
  ```

- [ ] **Step 5: Verificar que el documento es consistente**

  Lee el archivo completo de una vez y confirma:
  - No aparece `/ws/stream/<client_id>` ni `/ws/stream/{client_id}` en ningún lugar
  - `client_key` aparece en el Handshake de los ejemplos de la sección 1
  - Los diagramas de flujo en la sección 7 no mencionan la URL (no necesitan actualizarse)

  ```bash
  grep -n "client_id\|stream/" docs/client-protocol.md
  ```

  Espera: sin resultados (o solo menciones textuales sin ser URLs).

- [ ] **Step 6: Commit**

  ```bash
  git add docs/client-protocol.md
  git commit -m "docs(client-protocol): fix WS URL, add client_key/agent to handshake, clarify send vs text-direct"
  ```

---

## Self-Review

**Spec coverage:**
- ✓ Eliminar `docs/superpowers/` → Task 1
- ✓ Eliminar `.superpowers/sdd/` → Task 1
- ✓ Eliminar `.github/TASKS.md` → Task 1
- ✓ Reescribir README.md → Task 2
- ✓ Actualizar client-protocol.md → Task 3

**Placeholders:** Ninguno — todo el contenido de los archivos está escrito literalmente en el plan.

**Consistencia:**
- La URL `/ws/stream` (sin path param) aparece consistente en README.md y en client-protocol.md
- Los campos del handshake (`client_key`, `input_mode`, `output_mode`, `agent`) están documentados igual en ambos archivos
- La tabla de configuración en README.md contiene exactamente los campos de `src/core/config.py`
- El árbol de archivos en README.md refleja `src/` tal como existe en v1.6.4
