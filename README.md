# jota-gateway

BFF (Backend For Frontend) del ecosistema Jota IA. Punto de entrada único para todos los clientes: Jota-Screen (tele), jota-voice (Mac y Android), la app de iPhone (fase inicial) y Home Assistant. Un cliente ESP32 está planificado (*planned*) y sin desarrollar.

```
Jota-Screen / jota-voice / App iPhone / Home Assistant
        │
        ▼
  jota-gateway (FastAPI :8004)
  ├── /ws/stream          WebSocket — sesión interactiva de voz/texto
  ├── /healthz            Liveness probe
  ├── /ready              Readiness probe
  ├── /admin/*            Gestión y observabilidad (X-Admin-Token)
  └── /v1/*               OpenAI-compatible (Home Assistant, Open WebUI)
        │
        ▼
  ┌──────────────────────────────────────────┐
  │             Microservicios               │
  │  OpenClaw         WebSocket v4           │
  │  jota-transcriber WebSocket              │
  │  jota-speaker     WebSocket              │
  └──────────────────────────────────────────┘
```

No hay dependencia de `jota-db` — la identidad y configuración de clientes viven en una base de datos SQLite local (`<base>/data/gateway.db`, con `<base>` = `~/.jota-gateway` por defecto). Requiere OpenClaw **2026.6.11 o posterior** (detalles del protocolo en [`docs/openclaw-protocol.md`](docs/openclaw-protocol.md)).

---

## Endpoints

### WebSocket

| Endpoint | Auth | Descripción |
|----------|------|-------------|
| `ws://<host>:8004/ws/stream` | `client_key` en handshake JSON | Sesión interactiva de voz/texto |

Ver [`docs/client-protocol.md`](docs/client-protocol.md) para la guía completa de integración.

### Health

| Endpoint | Método | Auth | Descripción |
|----------|--------|------|-------------|
| `/healthz` | GET | ninguna | Liveness — siempre 200 si el proceso vive |
| `/ready` | GET | ninguna | Readiness — 200 ok/degraded, 503 si OpenClaw no responde |

```json
// GET /ready — ejemplo degradado
{
  "status": "degraded",
  "services": {
    "orchestrator": "ok",
    "transcriber": "unavailable",
    "tts": "ok"
  }
}
```

### Admin (requiere `X-Admin-Token`)

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/admin/sessions` | GET | Sesiones activas y recientes en memoria |
| `/admin/sessions/{id}` | GET | Detalle con eventos y latencias por turno |
| `/admin/orchestrators/{name}/status` | GET | Estado del orquestador (CONNECTED / RECONNECTING / DEGRADED) |
| `/admin/orchestrators/{name}/reconnect` | POST | Fuerza reconexión — responde 202 |
| `/admin/transcriber/status` | GET | Estado del proveedor STT activo (`provider`; con jota, reachability en vivo; con ElevenLabs, salud cacheada + `counters`) |
| `/admin/tts/status` | GET | Estado del proveedor TTS activo (`provider`, breaker, último error; con ElevenLabs, `counters`) |
| `/admin/speech` | GET | Proveedores de voz activos (`stt`, `tts`) y puerta de audio efectiva |
| `/admin/clients` | GET | Lista clientes (sin `client_key`; la huella `key_fp`) |
| `/admin/clients` | POST | Crear cliente; la respuesta trae su `client_key`, la única vez que se ve (se genera, o se importa una existente de ≥ 32 caracteres) |
| `/admin/clients/{id}` | GET | Detalle de un cliente |
| `/admin/clients/{id}` | PATCH | Actualizar cliente (campos parciales) |
| `/admin/clients/{id}` | DELETE | Borrar cliente |
| `/admin/clients/{id}/rotate-key` | POST | Rotar `client_key` (la respuesta trae la nueva) |
| `/admin/state` | GET | Instantánea viva: versión, uptime, estado de servicios, contadores y una fila por sesión |
| `/admin/state/stream` | GET | La misma instantánea por SSE cada segundo; cada stream termina solo a los 25 s, el cliente debe reconectar (`EventSource` lo hace solo) |

Los tres endpoints `*/status` comparten la forma `{"name", "state", "connected_at", "reconnect_attempts", "last_error"}`; los de transcriber y TTS añaden `provider` y, con ElevenLabs, `counters`.

Los campos de un cliente (`POST`/`PATCH /admin/clients`) están en `src/jota_gateway/models/admin_schemas.py` y sus defaults en `src/jota_gateway/db/models.py`. Desde la terminal, `jota-gw`. Los comandos de clientes y de estado necesitan el gateway en marcha y pasan por esta API (la CLI nunca escribe en la BD); `doctor` y `config` funcionan también con el gateway parado:

```bash
jota-gw                        # en una terminal: menú interactivo; en un script: la ayuda
jota-gw init                   # instalación nueva: <base>, config.env con ADMIN_TOKEN aleatorio y la BD
jota-gw serve [--create-db]    # arranca el gateway (puerto 8004, host GATEWAY_HOST)
jota-gw doctor                 # diagnóstico: configuración, BD, permisos, candado, gateway y servicios
jota-gw config show            # ajustes efectivos y su origen (entorno, config.env o por defecto)
jota-gw config get <AJUSTE> | validate
jota-gw config set <AJUSTE> [VALOR|-]   # escribe en config.env; unset <AJUSTE> lo quita
jota-gw install [--exe RUTA]   # servicio de usuario de systemd: lo instala y lo arranca
jota-gw start|stop|restart     # restart pregunta; --yes para scripts
jota-gw uninstall              # quita el servicio (pregunta); no toca los datos
jota-gw logs [-f] [-n N] [--level NIVEL] [--session ID]
jota-gw status                 # gateway, versión, orquestador, STT, TTS y sesiones
jota-gw version
jota-gw clients list           # nombre, id corto, tipo, activo, agente y huella fp
jota-gw clients show <ref>     # <ref>: id, prefijo de id (≥ 4) o nombre
jota-gw clients add --name … [--type ha|esp32|web|app] [--agent …] [--allowed-agent …] [--import-key]
jota-gw clients edit <ref> [--name …] [--set tts_speed=1.2 …] [--set output_mode:='["text"]']
jota-gw clients enable|disable <ref>
jota-gw clients rotate-key|remove <ref>    # preguntan; --yes para scripts
```

Opciones globales: `--home` (`JOTA_GATEWAY_HOME`, por defecto `~/.jota-gateway`), `--url` (`JOTA_GW_URL`, por defecto `http://127.0.0.1:8004`), `--token` (`JOTA_GW_TOKEN`; si no, `ADMIN_TOKEN` o `config.env`), `--json` y `--yes`. `JOTA_GATEWAY_HOME` la leen el gateway y la CLI; `JOTA_GW_URL` y `JOTA_GW_TOKEN`, solo la CLI. Códigos de salida: 0 bien, 1 error, 2 uso incorrecto, 3 gateway inalcanzable.

- **Configuración que leen la CLI y el gateway:** variables del proceso → `<base>/config.env` → valores por defecto, con `<base>` = `--home`, `JOTA_GATEWAY_HOME` o `~/.jota-gateway`. Nunca el `.env` del directorio actual. Una `DATABASE_URL` relativa se resuelve contra `<base>`. Los secretos (`*_TOKEN`, `*_KEY`) salen siempre enmascarados, también en `--json`.
- **`--set`:** `campo=valor` manda texto (el servidor convierte números y booleanos); `campo:=json` manda JSON (listas, `null`). Un campo desconocido sale con 2 y lista los editables.
- **`config set`:** valida el valor antes de escribir y rechaza saltos de línea. Los secretos (`*_TOKEN`, `*_KEY`) nunca van como argumento, para que no queden en el historial: se piden por prompt o se pasan por stdin con `-` (`echo -n … | jota-gw config set ADMIN_TOKEN -`). Si el servicio está activo, ofrece reiniciarlo; si no, el cambio se aplica al reiniciar (`jota-gw restart`). Avisa si la variable está también en el entorno, porque esa manda sobre `config.env`.
- **`--import-key`** lee la clave de stdin, nunca de un argumento.
- **En Docker:** `docker exec jota_gateway jota-gw …`. La imagen fija `JOTA_GATEWAY_HOME=/app`, así que `doctor` encuentra la BD del volumen. Sin `-t` no hay terminal: no se abre el menú y `rotate-key`/`remove` necesitan `--yes` (o usa `docker exec -it`). Con `GATEWAY_HOST` en una IP de la LAN, pasa `--url http://<esa IP>:8004`.

Auth: header `X-Admin-Token`. Sin header → 422. Token incorrecto → 401. `ADMIN_TOKEN` vacío → 503.

### OpenAI-compatible (`/v1/*`, para Home Assistant)

**Autenticación** (`src/jota_gateway/core/network.py`, `resolve_ha_caller` en `src/jota_gateway/api/openai_routes.py`):

- Origen **de confianza** (peer directo en las CIDR de `TRUSTED_NETWORKS`, peer loopback si `TRUST_LOOPBACK=true`, o un cliente avalado por un proxy de `TRUSTED_PROXIES` con `X-Real-IP` válido): no necesita credenciales. **Matiz (#216):** con `TRUSTED_PROXIES` por defecto (`127.0.0.1,::1`) un cliente directo en loopback *sin* `X-Real-IP` ya NO queda exento y debe enviar `Authorization: Bearer <client_key>`; sin proxy, vacía `TRUSTED_PROXIES=` para recuperar el comportamiento anterior (y con proxy, este debe sobrescribir `X-Real-IP`).

  | Escenario (config por defecto) | Antes | Ahora |
  |---|---|---|
  | Loopback directo, sin cabeceras (p. ej. HA en el mismo host) | 200 | **401** (Bearer) |
  | Loopback + solo `X-Forwarded-For` (proxy sin `X-Real-IP`) | 200 | **401** |
  | Loopback + `X-Real-IP` en `TRUSTED_NETWORKS` / externo | 200 / 401 | 200 / 401 |
  | Peer LAN directo en `TRUSTED_NETWORKS` | 200 | 200 |
  | `TRUSTED_PROXIES=` vacío, loopback directo | 200 | 200 |
- Cualquier otro origen debe enviar `Authorization: Bearer <client_key>`, validado contra la tabla `clients` (la misma que usa el handshake WebSocket). Sin él, o con una key inválida/inactiva → `401`.
- Si se envía un Bearer, siempre se valida, también desde un origen de confianza.
- Detrás de un proxy (nginx), la IP real solo se toma de `X-Real-IP` si el peer está en `TRUSTED_PROXIES`. **Falla cerrado (#216):** un peer de `TRUSTED_PROXIES` sin `X-Real-IP` válido no es origen de confianza (se exige Bearer); el proxy debe *sobrescribir* `X-Real-IP` (`proxy_set_header X-Real-IP $remote_addr;`), nunca reenviar el del cliente. `X-Forwarded-For` nunca concede confianza (`jota-gw serve` arranca uvicorn sin `proxy_headers`). Consecuencia: un cliente directo en loopback (p. ej. HA en el mismo host, sin proxy) ya no queda exento con la config por defecto; envía Bearer, o vacía `TRUSTED_PROXIES` si no hay proxy.
- Host de escucha de `jota-gw serve` (también en Docker): `GATEWAY_HOST` (default `0.0.0.0`); detrás de un proxy en el mismo host usa `GATEWAY_HOST=127.0.0.1`.
- `TRUSTED_NETWORKS` vacío = fail-closed: solo loopback puede quedar exento (sujeto al matiz de `TRUSTED_PROXIES` de arriba).
- `HANDSHAKE_MAX_BYTES` mide **caracteres**, no bytes (con caracteres multibyte el tope real puede ser hasta 4x mayor). El tope de 1 MiB por mensaje WebSocket lo pone `jota-gw serve`: con `uvicorn` a mano rige el de uvicorn (16 MiB).
- Con Bearer válido, `model` selecciona el agente y se aplican `default_agent`/`allowed_agents` del cliente (violación → `403`).

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/v1/models` | GET | Devuelve el alias `jota-gateway` y los agentes del roster de OpenClaw (filtrados por `allowed_agents`) para un llamante con Bearer; un origen confiable sin Bearer (el chat ignora `model` y usa el agente por defecto) y un gateway sin roster, solo el alias |
| `/v1/chat/completions` | POST | Chat completion; soporta `stream: true/false` |

---

## Protocolo WebSocket — resumen rápido

El cliente envía un handshake JSON como primer mensaje. Si es válido, el gateway responde con `ready` y la sesión comienza.

**Cada turno de respuesta:**
```
← {"type":"turn_start","turn_id":"t-1","turn_seq":1}
← {"type":"token","turn_id":"t-1","text":"Hola,"}
← [0xA1][0x00][0x01][PCM16 24kHz...]   ← audio binario con header
← {"type":"turn_end","turn_id":"t-1"}
```

Ver [`docs/client-protocol.md`](docs/client-protocol.md) para la referencia completa incluyendo barge-in, transcripción, push turns y manejo de errores.

---

## Quick start

```bash
uv sync --extra dev   # crea .venv desde uv.lock (o `pip install -e .[dev]`, sin versiones fijadas)
source .venv/bin/activate
jota-gw init        # una vez: crea ~/.jota-gateway (o JOTA_GATEWAY_HOME), config.env y la BD
jota-gw serve       # en desarrollo, con recarga: uvicorn jota_gateway.main:app --port 8004 --reload
# o con Docker:
docker compose up
```

### Instalación nativa

Con las mismas versiones que la imagen Docker: cada release publica `constraints.txt` (exportado de `uv.lock`, sin hashes porque uv no los admite en constraints) y `-c` acepta la URL directamente.

```bash
V=1.x.y
uv tool install "jota-gateway @ git+https://github.com/Jota-project/jota-gateway@v$V" \
  -c https://github.com/Jota-project/jota-gateway/releases/download/v$V/constraints.txt
```

> **El gateway nunca crea una BD por su cuenta.** Si no la encuentra en `<base>/data/gateway.db`
> (o en la `DATABASE_URL` configurada), se para y explica qué hacer: así no arranca vacío, sin
> clientes, por haberse equivocado de directorio. La crea `jota-gw init`. La imagen Docker
> arranca con `jota-gw serve --create-db`, así que `docker compose up` con un `./data` vacío
> funciona como siempre.

### Servicio (systemd de usuario)

Solo en Linux con systemd; en Docker usa `docker compose`. `jota-gw install` crea `~/.config/systemd/user/jota-gateway.service` y lo arranca. Exige una config válida, la BD ya creada (`jota-gw init`; `install` nunca la crea) y el puerto 8004 libre (para el contenedor antes). Si el servicio ya es el nuestro y está activo, reinstala, y si la unidad cambia, lo reinicia. `uninstall` y sobrescribir una unidad distinta con `install` piden confirmación (para scripts, `jota-gw --yes …`).

- **Ejecutable:** usa el `jota-gw` del PATH. Rechaza uno dentro de un clon o de una instalación editable, porque la unidad se rompería al moverlo: instala con `uv tool install` o pasa `--exe RUTA`.
- **Linger:** sin `loginctl enable-linger $USER` el servicio se para al cerrar tu sesión; `install` avisa si falta.
- **Registros:** `jota-gw logs` lee el journal. `--level` y `--session` filtran por nivel mínimo y por sesión o cliente, tanto con `LOG_FORMAT=json` como con texto.
- **Desinstalar:** `jota-gw uninstall` quita la unidad y deja `<base>` intacta.

> **Docker:** el contenedor corre como no-root. Antes del primer `docker compose up`
> define `GATEWAY_UID`/`GATEWAY_GID` en `.env` con el dueño de `./data`
> (`echo "GATEWAY_UID=$(id -u)" >> .env && echo "GATEWAY_GID=$(id -g)" >> .env`).
> Si `./data` viene de un despliegue anterior como root: `sudo chown -R "$(id -u):$(id -g)" data`.
> Detalles en `docker-compose.yml`.

---

## Configuración

Variables de entorno o, si no, de `<base>/config.env` (instalación nativa). En Docker van en el `.env` junto a `docker-compose.yml`, que las pasa como variables. Formato `host:port` sin protocolo para todos los URLs. Esta tabla es un resumen: la referencia completa, con todos los plazos y sus defaults, es [`.env.sample`](.env.sample).

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///data/gateway.db` | Base de datos SQLite local (identidad y configuración de clientes). Una ruta relativa cuelga de `<base>` |
| `GATEWAY_HOST` | `0.0.0.0` | Host de escucha de `jota-gw serve` (`127.0.0.1` detrás de un proxy en el mismo host) |
| `TRANSCRIBER_WS_URL` | `localhost:9000` | jota-transcriber |
| `TTS_WS_URL` | `localhost:8005` | jota-speaker |
| `TTS_TOKEN` | `gateway` | Token de auth para jota-speaker |
| `OPENCLAW_HOST` | `127.0.0.1` | Host de OpenClaw |
| `OPENCLAW_PORT` | `18789` | Puerto de OpenClaw |
| `OPENCLAW_TOKEN` | — | Token de auth para OpenClaw (obligatorio) |
| `ADMIN_TOKEN` | — | Token para endpoints `/admin/*`; vacío deshabilita el admin API |
| `ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial en reconexión del orquestador (s) |
| `ORCHESTRATOR_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo en reconexión del orquestador (s); también el periodo del reintento lento en DEGRADED |
| `ORCHESTRATOR_RECONNECT_MAX_DURATION` | `300.0` | Tiempo hasta DEGRADED del orquestador (s); en DEGRADED sigue reintentando un intento cada `MAX_BACKOFF` s |
| `SINGLE_PROCESS_LOCK` | `true` | `flock` sobre `gateway.lock` junto a la BD: un segundo proceso (p. ej. `--workers 2`) no arranca. Multi-worker no está soportado |
| `SESSION_REVALIDATE_INTERVAL_S` | `10.0` | Cada sesión WS se revalida contra la BD; cliente borrado/desactivado/clave rotada → cierre 1008 `revoked` |
| `TRANSCRIBER_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial en reconexión del transcriptor (s) |
| `TRANSCRIBER_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo en reconexión del transcriptor (s) |
| `TRANSCRIBER_RECONNECT_MAX_DURATION` | `300.0` | Tiempo hasta DEGRADED del transcriptor (s) |
| `TTS_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial del breaker de TTS (s) |
| `TTS_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo del breaker de TTS (s) — sin estado DEGRADED, cada turno elegible reintenta |

### Proveedores de voz (STT/TTS)

El STT y el TTS pasan por una capa de proveedores (`src/jota_gateway/services/speech/`). Los clientes no notan la diferencia: mismos formatos de audio y mismos mensajes.

| Variable | Default | Descripción |
|----------|---------|-------------|
| `STT_PROVIDER` | `jota` | `jota` (jota-transcriber) o `elevenlabs` (Scribe v2 Realtime) |
| `TTS_PROVIDER` | `jota` | `jota` (jota-speaker) o `elevenlabs` (stream-input, `eleven_flash_v2_5`) |
| `ELEVENLABS_API_KEY` | — | Obligatoria si algún proveedor es `elevenlabs` (permisos Text to Speech y Speech to Text) |
| `ELEVENLABS_TTS_DEFAULT_VOICE` | `gc61tTk93h3LBXPxew2V` | Voz por defecto; las voces de clientes que no son de ElevenLabs (p. ej. `af_heart`) caen en ella o se traducen con `TTS_VOICE_MAP` |
| `STT_AUDIO_GATE` | `energy` | Puerta de energía del STT de ElevenLabs (solo se envía audio mientras se habla); `off` la quita. jota no la usa |
| `STT_GATE_RMS_DBFS` | `-45` | Umbral de la puerta de energía |

`ELEVENLABS_BASE_URL` y `ELEVENLABS_TTS_MODEL` están en `.env.sample`; el resto de ajustes de ElevenLabs son constantes del adaptador (`src/jota_gateway/services/speech/elevenlabs/`). El proveedor se elige con `STT_PROVIDER`/`TTS_PROVIDER` al arrancar; cambiarlo exige reiniciar. Con el plan Creator, ElevenLabs conserva el audio y las transcripciones (la retención cero es solo enterprise).

`silence_timeout_s` y `max_silence_turns` (umbral del watchdog de silencio) son campos de configuración **por cliente** en la base de datos, no variables de entorno globales — ver `ClientRecord` en `src/jota_gateway/db/models.py`.

---

## Desarrollo y tests

### Dependencias

`pyproject.toml` declara los rangos y **`uv.lock` fija las versiones exactas**, con hashes: el CI (`uv sync --locked`), la imagen Docker (`uv export` + `pip install --require-hashes`) y Dependabot (ecosistema `uv`) parten de él. Al cambiar una dependencia en `pyproject.toml`, ejecuta `uv lock` y commitea los dos ficheros; el CI falla si `uv.lock` no corresponde a `pyproject.toml`.

```bash
uv sync --extra dev    # entorno de desarrollo desde uv.lock
uv lock --upgrade      # sube todo dentro de los rangos (Dependabot lo hace por paquete)
```

### Tests

```bash
# Tests (pytest.ini excluye por defecto los marcadores e2e_real y live)
PYTHONPATH=. pytest

# Test específico
PYTHONPATH=. pytest tests/integration/test_rest_health.py

# Lint
ruff check src/ tests/
```

---

## Smoke-test E2E real (contra OpenClaw de producción)

`tests/e2e/` valida el pipeline completo contra el OpenClaw **real** de este mismo
servidor: turno de texto, cancelación, sesiones concurrentes y uso de tools. Nunca
corre automáticamente — ni en la CI normal, ni con un `pytest` a secas (marker
`e2e_real`, deseleccionado por defecto en `pytest.ini`).

Solo se dispara manualmente vía el workflow `E2E Real Smoke Test`
(`workflow_dispatch`), gated por un GitHub Environment con aprobación requerida.
Antes de poder usarlo hace falta configurar, una sola vez, lo siguiente
(fuera de este repo):

1. **Agente de test dedicado en OpenClaw** — con personalidad/memoria aislada de
   los agentes de producción, y alguna tool/skill simple y determinista habilitada
   (para `test_tool_use.py`). Nunca se testea contra un agente real.
2. **Runner self-hosted dedicado**, separado del runner `green-house` existente
   (que sirve a otro repo). Registrar una segunda instancia de
   [`actions-runner`](https://github.com/actions/runner) en este servidor, con
   label `jota-e2e`, contra este repo.
3. **GitHub Environment `e2e-real-production`** (Settings → Environments):
   - *Required reviewers*: el usuario propietario del repo, explícitamente.
   - *Deployment branches*: restringido a `main`.
   - *Environment variable* `E2E_TEST_AGENT`: nombre del agente de test del punto 1.

Ejecución manual local (sin pasar por GitHub Actions), útil para validar cambios
en la suite antes de fiarse del workflow:

```bash
set -a; . ./.env; set +a      # o el fichero donde tengas ADMIN_TOKEN y OPENCLAW_TOKEN
PYTHONPATH=. E2E_TEST_AGENT=<agente-de-test> pytest tests/e2e -m e2e_real -v
```

`OPENCLAW_TOKEN` y `ADMIN_TOKEN` nunca se guardan en GitHub: el proceso de test los
lee de sus variables de entorno. La suite no lee ningún `.env` ni `config.env` por su
cuenta (`tests/conftest.py` fija una base temporal), por eso hay que exportarlos.

**Nota para el workflow de GitHub Actions:** el checkout que hace `actions/checkout`
en el runner es limpio y **no incluye `.env`** (está en `.gitignore`). Por eso, para
la ruta de CI, `ADMIN_TOKEN` y `OPENCLAW_TOKEN` deben estar exportados como
variables de entorno reales en el propio proceso del runner self-hosted (p. ej.
vía `Environment=` en su unidad systemd) — no en un `.env` dentro del checkout ni
en GitHub Secrets. El job incluye un paso de verificación previo al de tests que
falla explícitamente (`exit 1`) si alguna de las dos falta, para que un runner mal
configurado rompa de forma visible en lugar de pasar en silencio sin cobertura
real.

---

## Arquitectura interna

El gateway instancia un `JotaBridge` por cada sesión WebSocket. Los tres servicios downstream (OpenClaw, Transcriber, TTS) tienen reconexión automática con backoff exponencial, unificada en `services/reconnection.py` (estados compartidos CONNECTED / RECONNECTING / DEGRADED). OpenClaw y los proveedores de voz son singletons de proceso; con el proveedor `jota`, cada sesión de audio abre su propio `ReconnectingTranscriberClient` y cada turno su propio `TTSClient`.

```
src/jota_gateway/
├── paths.py                 directorio base, config.env y ruta de la BD (compartido con la CLI)
├── alembic.ini, migrations/ migraciones de Alembic, dentro del paquete instalado
├── api/
│   ├── routes.py            WebSocket /ws/stream — handshake, ready, bridge lifecycle
│   ├── admin_routes.py      /admin/* — CRUD de clientes + observabilidad (sesiones, orquestador, transcriptor, TTS)
│   ├── openai_routes.py     /v1/models, /v1/chat/completions
│   ├── health_routes.py     /healthz, /ready
│   └── deps.py              get_admin_auth — dependencia de auth para admin
├── core/
│   ├── config.py            Settings (pydantic-settings, <base>/config.env)
│   ├── agent_policy.py      resolve_agent() — cascada y política de agentes
│   ├── network.py           resolve_client_ip() / origen de confianza de /v1
│   ├── startup_checks.py    lock de proceso único y comprobaciones de arranque
│   └── session_key.py       make_session_key() — formato canónico de session key
├── db/
│   ├── models.py            ClientRecord (SQLModel) — identidad y config por cliente
│   └── database.py          get_engine() / run_migrations() / get_db_session()
├── cli/                     jota-gw — init, serve, menú, doctor, config, status, version, clients, service (servicio systemd) y logs
└── services/
    ├── bridge.py            JotaBridge — coordinador de sesión WS
    ├── db_client.py         DbClient — lee ClientRecord de SQLite local (singleton, sin caché)
    ├── outbound.py          QueuedSender — salida serializada hacia cada cliente
    ├── reconnection.py      ConnectionState / ServiceStatus / to_wire_state() — compartido por los 3 wrappers
    ├── transcriber_client.py       WebSocket → jota-transcriber, protocolo puro (sin reconexión)
    ├── transcriber_reconnecting.py ReconnectingTranscriberClient — uno por sesión de audio, backoff en background
    ├── tts_client.py                WebSocket → jota-speaker, protocolo puro, creado por turno
    ├── tts_reconnecting.py          ReconnectingTTSClient — singleton, backoff perezoso por turno
    ├── orchestration.py     call_orchestrator() — helper compartido WS+HTTP
    ├── pipeline_tracker.py  PipelineTracker — latencias por turno
    ├── session_registry.py  SessionRegistry — observabilidad de sesiones
    ├── speech/              Proveedores STT/TTS: ports.py, factory.py, jota.py, elevenlabs/
    └── openclaw/
        ├── client.py        OpenClawClient — WebSocket v4, multiplexado
        ├── reconnecting.py  ReconnectingOpenClawClient — backoff + DEGRADED
        ├── dispatcher.py    FrameDispatcher — enruta frames a turn/client registry
        ├── registry.py      TurnRegistry + ClientRegistry (+ broadcast_status a todas las sesiones)
        └── models.py        GatewayInfo
```

## Licencia

MIT. Ver [LICENSE](LICENSE).
