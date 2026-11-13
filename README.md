# Fogar Gateway

La puerta de entrada de Fogar en casa. Un solo servicio que reconoce cada aparato del hogar, lo conecta con el asistente y con la voz, y se administra desde el navegador o la terminal.

- **Aparatos**: Fogar TV, el cliente de voz (Mac y Android), la app de iPhone y Home Assistant. Un aparato nuevo **descubre el gateway y se empareja con un código de 6 cifras**, sin copiar claves.
- **Asistente**: los turnos de voz y texto van a [OpenClaw](docs/openclaw-protocol.md), que elige el agente y sus herramientas. El gateway lleva el turno de principio a fin: streaming, interrupciones (barge-in) y avisos que llegan sin preguntar (push).
- **Voz**: transcripción (STT) y síntesis (TTS) con los servicios propios de Fogar, con ElevenLabs o desactivadas. Sin voz, el texto sigue funcionando.
- **Administración**: un panel web en `/` (estado en vivo, sesiones y clientes) y la CLI `fogar-gw` (diagnóstico, configuración, servicio, logs y emparejamiento).
- **Instalación**: un comando instala, verifica OpenClaw y la voz contra los servicios reales, lo deja como servicio de usuario y te dice cómo llegar al panel: desde casa, por Tailscale o por tu propio dominio.

```
  Aparatos de casa                    Fogar Gateway  :8004                     Servicios
  ────────────────                    ────────────────────                     ─────────
  Fogar TV           ──┐          ┌── /ws/stream      voz y texto en vivo  ──┐
  Voz (Mac, Android) ──┤ descubren│   /.well-known    descubrimiento        │──► OpenClaw (agentes)
  App iPhone         ──┤ y se     │   /pair           emparejamiento        │──► STT: propio · ElevenLabs · off
  Home Assistant     ──┘ emparejan└── /v1/*           API compatible OpenAI ┘──► TTS: propio · ElevenLabs · off

  Tú, desde casa, Tailscale o tu dominio:
    navegador ──► /           panel web (estado, sesiones, clientes)
    terminal  ──► fogar-gw    CLI sobre /admin/* (doctor, config, servicio, logs, pair)
```

La identidad y la configuración de cada aparato viven en una base de datos SQLite local (`<base>/data/gateway.db`, con `<base>` = `~/.fogar-gateway` por defecto). Requiere OpenClaw **2026.6.11 o posterior**.

```bash
curl -fsSL https://github.com/fogarhome/fogar-gateway/releases/latest/download/install.sh | sh
```

Detalles en [Instalación](#instalación). Si integras un aparato nuevo, el protocolo está en [`docs/client-protocol.md`](docs/client-protocol.md).

---

## Endpoints

### Web

`http://<host>:8004/` es el panel de Fogar: se entra con el token de administrador (`ADMIN_TOKEN`) y desde ahí se ve el estado en vivo y las sesiones, y se emparejan, crean, editan, rotan y borran clientes. Lo sirve el propio gateway desde su paquete, sin Node en la máquina; las rutas de la API de abajo tienen siempre prioridad. Si el paquete no lleva la web compilada (un clon sin build), `/` da 404, el gateway funciona igual y lo avisa una vez en el log. Para desarrollarla, [`web/README.md`](web/README.md).

### WebSocket

| Endpoint | Auth | Descripción |
|----------|------|-------------|
| `ws://<host>:8004/ws/stream` | `client_key` en handshake JSON | Sesión interactiva de voz/texto |

Ver [`docs/client-protocol.md`](docs/client-protocol.md) para la guía completa de integración.

### Health

| Endpoint | Método | Auth | Descripción |
|----------|--------|------|-------------|
| `/healthz` | GET | ninguna | Liveness — siempre 200 si el proceso vive |
| `/ready` | GET | ninguna | Readiness — 200 ok/degraded, 503 si OpenClaw no responde. Un proveedor de voz en `off` sale `unavailable` en `services` pero no degrada `status` |

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

### Descubrimiento y emparejamiento (públicos)

| Endpoint | Método | Auth | Descripción |
|----------|--------|------|-------------|
| `/.well-known/fogar-gateway` | GET | ninguna | Identifica al gateway: `{"service": "fogar-gateway", "version", "name", "instance_id"}` (`name` = `GATEWAY_NAME` o el nombre del equipo; `instance_id` = identificador aleatorio por arranque del proceso) |
| `/pair` | POST | un código de `fogar-gw pair` | El cliente canjea el código y recibe su `client_key`, la única vez que se ve. Ver [Emparejar un cliente](#emparejar-un-cliente) |

Los dos son públicos: detrás de un proxy o un túnel también llegan desde internet. `/pair` solo acepta un código abierto por el administrador, y 5 códigos erróneos seguidos anulan todos los abiertos. El protocolo para clientes está en [`docs/client-protocol.md` §0](docs/client-protocol.md#0-descubrir-el-gateway-y-emparejarse).

### Admin (requiere `X-Admin-Token`)

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/admin/sessions` | GET | Sesiones activas y recientes en memoria |
| `/admin/sessions/{id}` | GET | Detalle con eventos y latencias por turno |
| `/admin/orchestrator/status` | GET | Estado del orquestador (CONNECTED / RECONNECTING / DEGRADED) |
| `/admin/orchestrator/reconnect` | POST | Fuerza reconexión — responde 202 |
| `/admin/transcriber/status` | GET | Estado del proveedor STT activo (`provider`; con fogar, reachability en vivo; con ElevenLabs, salud cacheada + `counters`) |
| `/admin/tts/status` | GET | Estado del proveedor TTS activo (`provider`, breaker, último error; con ElevenLabs, `counters`) |
| `/admin/speech` | GET | Proveedores de voz activos (`stt`, `tts`) y puerta de audio efectiva |
| `/admin/clients` | GET | Lista clientes (sin `client_key`; la huella `key_fp`) |
| `/admin/clients` | POST | Crear cliente; la respuesta trae su `client_key`, la única vez que se ve (se genera, o se importa una existente de ≥ 32 caracteres) |
| `/admin/clients/{id}` | GET | Detalle de un cliente |
| `/admin/clients/{id}` | PATCH | Actualizar cliente (campos parciales) |
| `/admin/clients/{id}` | DELETE | Borrar cliente |
| `/admin/clients/{id}/rotate-key` | POST | Rotar `client_key` (la respuesta trae la nueva) |
| `/admin/pairing` | POST | Abrir un código de emparejamiento con la plantilla del cliente (los campos de `POST /admin/clients`, nombre opcional y sin clave). La respuesta trae el código, la única vez que se ve. Como mucho 5 abiertos (409) |
| `/admin/pairing` | GET | Códigos abiertos (sin el código) |
| `/admin/pairing/{id}` | GET | Estado de un código: `open`, `used` (con `client_id` y `client_name`), `expired` o `cancelled` |
| `/admin/pairing/{id}` | DELETE | Anular un código abierto |
| `/admin/state` | GET | Instantánea viva: versión, uptime, flujos `/admin/state/stream` abiertos (`gateway.dashboards`), estado de servicios, contadores y una fila por sesión |
| `/admin/state/stream` | GET | La misma instantánea por SSE cada segundo; cada stream termina solo a los 25 s, el cliente debe reconectar (`EventSource` lo hace solo) |
| `/admin/access` | GET | Vías para llegar al gateway (`public`, `tailscale_https`, `tailscale`, `lan`), cada una con su estado (`ok`, `protegida`, `otro_gateway`, `inalcanzable`, `sin_verificar`) y la recomendada; `{"docker": bool, "routes": [...]}`, en caché 15 s |

Los tres endpoints `*/status` comparten la forma `{"name", "state", "connected_at", "reconnect_attempts", "last_error"}`; los de transcriber y TTS añaden `provider` y, con ElevenLabs, `counters`.

Los campos de un cliente (`POST`/`PATCH /admin/clients`) están en `src/fogar_gateway/models/admin_schemas.py` y sus defaults en `src/fogar_gateway/db/models.py`. Desde la terminal, `fogar-gw`. Los comandos de clientes y de estado necesitan el gateway en marcha y pasan por esta API (la CLI nunca escribe en la BD); `doctor` y `config` funcionan también con el gateway parado:

```bash
fogar-gw                        # en una terminal: menú interactivo; en un script: la ayuda
fogar-gw init                   # instalación nueva: <base>, config.env con ADMIN_TOKEN aleatorio y la BD
fogar-gw onboard [--answers FICHERO]   # asistente: OpenClaw, voz, servicio y dashboard
fogar-gw token                  # el token del dashboard (ADMIN_TOKEN), solo en una terminal
fogar-gw access                 # desde dónde se llega al gateway, verificado, con la vía recomendada
fogar-gw access setup           # elige cómo entrar (Tailscale o tu dominio) y después lista las vías
fogar-gw serve [--create-db]    # arranca el gateway (puerto 8004, host GATEWAY_HOST)
fogar-gw doctor                 # diagnóstico: configuración, BD, permisos, candado, gateway y servicios
fogar-gw config show            # ajustes efectivos y su origen (entorno, config.env o por defecto)
fogar-gw config get <AJUSTE> | validate
fogar-gw config set <AJUSTE> [VALOR|-]   # escribe en config.env; unset <AJUSTE> lo quita
fogar-gw install [--exe RUTA]   # servicio de usuario de systemd: lo instala y lo arranca
fogar-gw start|stop|restart     # restart pregunta; en scripts: fogar-gw --yes restart
fogar-gw uninstall              # quita el servicio (pregunta); no toca los datos
fogar-gw migrate --from-docker DIR   # copia una instalación Docker (.env y BD) a la nativa
fogar-gw logs [-f] [-n N] [--level NIVEL] [--session ID]
fogar-gw status                 # gateway, versión, orquestador, STT, TTS y sesiones
fogar-gw version
fogar-gw clients list           # nombre, id corto, tipo, activo, agente y huella fp
fogar-gw clients show <ref>     # <ref>: id, prefijo de id (≥ 4) o nombre
fogar-gw clients add --name … [--type ha|esp32|web|app|screen] [--agent …] [--allowed-agent …] [--import-key]
fogar-gw clients edit <ref> [--name …] [--set tts_speed=1.2 …] [--set output_mode:='["text"]']
fogar-gw clients enable|disable <ref>
fogar-gw clients rotate-key|remove <ref>    # preguntan; en scripts: fogar-gw --yes clients remove <ref>
fogar-gw pair [--name …] [--type …] [--agent …] [--set …] [--no-wait]   # código para que un cliente se dé de alta solo
```

Opciones globales: van **antes** del subcomando (`fogar-gw --yes clients remove X`; después, click no las reconoce): `--home` (`FOGAR_GATEWAY_HOME`, por defecto `~/.fogar-gateway`), `--url` (`FOGAR_GW_URL`, por defecto `http://127.0.0.1:8004`), `--token` (`FOGAR_GW_TOKEN`; si no, `ADMIN_TOKEN` o `config.env`), `--json` y `--yes`. `FOGAR_GATEWAY_HOME` la leen el gateway y la CLI; `FOGAR_GW_URL` y `FOGAR_GW_TOKEN`, solo la CLI. Códigos de salida: 0 bien, 1 error, 2 uso incorrecto, 3 gateway inalcanzable.

- **Configuración que leen la CLI y el gateway:** variables del proceso → `<base>/config.env` → valores por defecto, con `<base>` = `--home`, `FOGAR_GATEWAY_HOME` o `~/.fogar-gateway`. Nunca el `.env` del directorio actual. Una `DATABASE_URL` relativa se resuelve contra `<base>`. Los secretos (`*_TOKEN`, `*_KEY`) salen siempre enmascarados, también en `--json`.
- **`--set`:** `campo=valor` manda texto (el servidor convierte números y booleanos); `campo:=json` manda JSON (listas, `null`). Un campo desconocido sale con 2 y lista los editables.
- **`config set`:** valida el valor antes de escribir y rechaza saltos de línea. Los secretos (`*_TOKEN`, `*_KEY`) nunca van como argumento, para que no queden en el historial: se piden por prompt oculto (`fogar-gw config set ADMIN_TOKEN`) o, en scripts, se leen de un fichero por stdin (`fogar-gw config set ADMIN_TOKEN - < fichero`); un `echo` dejaría el secreto en el historial. Si el servicio está activo, ofrece reiniciarlo (como `fogar-gw restart`: espera a `/healthz` y sale con 1 si no arranca, con el cambio ya guardado); si no, el cambio se aplica al reiniciar (`fogar-gw restart`). Avisa si la variable está también en el entorno, porque esa manda sobre `config.env`.
- **`--import-key`** lee la clave de stdin, nunca de un argumento.
- **En Docker:** `docker exec fogar_gateway fogar-gw …`. La imagen fija `FOGAR_GATEWAY_HOME=/app`, así que `doctor` encuentra la BD del volumen. Sin `-t` no hay terminal: no se abre el menú y `rotate-key`/`remove` necesitan `--yes` (o usa `docker exec -it`). Con `GATEWAY_HOST` en una IP de la LAN, pasa `--url http://<esa IP>:8004`.

Auth: header `X-Admin-Token`. Sin header → 422. Token incorrecto → 401. `ADMIN_TOKEN` vacío → 503.

### OpenAI-compatible (`/v1/*`, para Home Assistant)

**Autenticación** (`src/fogar_gateway/core/network.py`, `resolve_ha_caller` en `src/fogar_gateway/api/openai_routes.py`):

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
- Detrás de un proxy (nginx), la IP real solo se toma de `X-Real-IP` si el peer está en `TRUSTED_PROXIES`. **Falla cerrado (#216):** un peer de `TRUSTED_PROXIES` sin `X-Real-IP` válido no es origen de confianza (se exige Bearer); el proxy debe *sobrescribir* `X-Real-IP` (`proxy_set_header X-Real-IP $remote_addr;`), nunca reenviar el del cliente. `X-Forwarded-For` nunca concede confianza (`fogar-gw serve` arranca uvicorn sin `proxy_headers`). Consecuencia: un cliente directo en loopback (p. ej. HA en el mismo host, sin proxy) ya no queda exento con la config por defecto; envía Bearer, o vacía `TRUSTED_PROXIES` si no hay proxy.
- Host de escucha de `fogar-gw serve` (también en Docker): `GATEWAY_HOST` (default `0.0.0.0`); detrás de un proxy en el mismo host usa `GATEWAY_HOST=127.0.0.1`.
- `TRUSTED_NETWORKS` vacío = fail-closed: solo loopback puede quedar exento (sujeto al matiz de `TRUSTED_PROXIES` de arriba).
- `HANDSHAKE_MAX_BYTES` mide **caracteres**, no bytes (con caracteres multibyte el tope real puede ser hasta 4x mayor). El tope de 1 MiB por mensaje WebSocket lo pone `fogar-gw serve`: con `uvicorn` a mano rige el de uvicorn (16 MiB).
- Con Bearer válido, `model` selecciona el agente y se aplican `default_agent`/`allowed_agents` del cliente (violación → `403`).

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/v1/models` | GET | Devuelve el alias `fogar-gateway` y los agentes del roster de OpenClaw (filtrados por `allowed_agents`) para un llamante con Bearer; un origen confiable sin Bearer (el chat ignora `model` y usa el agente por defecto) y un gateway sin roster, solo el alias |
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

## Instalación

La vía recomendada es la **instalación nativa** (servicio de usuario de systemd, solo Linux). Docker queda como [alternativa](#docker-alternativa). `<base>` es `~/.fogar-gateway` (o `--home` / `FOGAR_GATEWAY_HOME`).

### Instalación nativa

**Requisitos:** Linux (x86_64 o aarch64) con systemd de usuario y un OpenClaw en este mismo equipo (o accesible por túnel SSH), 2026.6.11 o posterior. Lo demás lo comprueba el instalador. Ejecútalo con tu usuario, no como root: el servicio es de tu usuario.

```bash
curl -fsSL https://github.com/fogarhome/fogar-gateway/releases/latest/download/install.sh | sh
```

Qué hace `install.sh`:

1. Comprueba Linux, la arquitectura y `systemctl --user`. Si falta `git` (`sudo apt-get install -y git`) o linger (`sudo loginctl enable-linger $(id -un)`; sin él, el servicio se para al cerrar tu sesión), enseña el comando y pregunta «¿lo ejecuto yo? [s/N]»: nada con `sudo` sin una `s`. Sin terminal, o con otra respuesta, sale y te dice qué ejecutar.
2. Instala [uv](https://docs.astral.sh/uv/) si falta, y `fogar-gw` con `uv tool install` en la versión de la release, con su `constraints.txt` (las mismas versiones que la imagen Docker). `uv tool update-shell` deja `~/.local/bin` en el PATH de las terminales nuevas; si la tuya aún no lo tiene, el instalador acaba diciéndolo (abre otra terminal o usa `~/.local/bin/fogar-gw`).
3. La primera vez lanza el asistente, `fogar-gw onboard` (abajo). Sin terminal (p. ej. `ssh host 'curl … | sh'`) se para ahí: «Instalado. Configúralo con `fogar-gw onboard` o, si vienes de Docker, `fogar-gw migrate --from-docker DIR` y `fogar-gw install`.»

**Volver a ejecutarlo actualiza:** con el servicio ya instalado, reinstala `fogar-gw`, reinicia el servicio (corta las sesiones abiertas) y pasa `fogar-gw doctor`, sin preguntar nada y sin tocar `config.env`.

**Versión:** el `install.sh` de `releases/latest` instala la última release, y el de una release concreta (`…/releases/download/vX.Y.Z/install.sh`), la suya. Para elegir otra: `curl -fsSL … | FOGAR_VERSION=X.Y.Z sh`. Con `sh -s -- --no-onboard` solo instala `fogar-gw`, sin el asistente (es el primer paso para [pasar de Docker a nativo](#pasar-de-docker-a-nativo)); las versiones anteriores a `fogar-gw onboard` (desde la v1.26.0, la primera con `constraints.txt`) solo se instalan con `--no-onboard` o sin terminal.

#### El asistente: `fogar-gw onboard`

`fogar-gw onboard` pregunta lo mínimo y **verifica cada proveedor contra el servicio real antes de guardarlo** en `config.env`:

1. Si falta `config.env` o la BD, prepara `<base>` como `fogar-gw init` (no toca lo que ya exista).
2. OpenClaw: lo busca en este equipo (si no, pregunta host y puerto) y pide su token (`openclaw config get gateway.auth.token`). Solo en loopback: para un OpenClaw de otra máquina, un túnel SSH (`ssh -L 18789:127.0.0.1:18789 …`). Sin OpenClaw verificado no sigue.
3. STT y TTS: servicio propio (`host:puerto` y, si la usa, su clave), ElevenLabs (una sola clave para los dos) o «ahora no» (`off`: sin STT no hay transcripción y sin TTS las respuestas llegan solo en texto).
4. «¿Cómo vas a entrar a Fogar?»: desde casa (red local) siempre; además, si los marcas:
   - **Con Tailscale:** si `tailscale serve` ya lleva al gateway, te da su `https://…` y no toca nada. Si no, pregunta antes de activarlo en 443 (o en 8443 si 443 ya sirve otra cosa; si los dos están ocupados, no lo toca). Si Tailscale pide permiso, te enseña `sudo tailscale set --operator=$USER` y lo ejecuta solo si eliges «Ejecutarlo ahora» (y reintenta una vez); si la tailnet no tiene HTTPS, te da el enlace para activarlo. Sin Tailscale te da los comandos oficiales para instalarlo (`curl -fsSL https://tailscale.com/install.sh | sh` y `sudo tailscale up`) y vuelve a mirar cuando quieras, o sigues sin él: nunca lo instala por ti.
   - **Con mi propio dominio:** una URL `https://` (sin ruta, parámetros ni usuario) que tu proxy haga llegar a `http://localhost:8004`; se añade a `PUBLIC_URLS` sin quitar las que ya hay.

   Lo mismo, en cualquier momento: `fogar-gw access setup`.
5. Instala el servicio, o lo reinicia si ya estaba instalado (con terminal pregunta antes, porque corta las sesiones abiertas), y pasa `fogar-gw doctor`.
6. Termina en el dashboard:
   - las vías para llegar al dashboard desde otro aparato: la recomendada («Tu dashboard») y las demás, cada una con su estado (verificada, sin comprobar…). Por la LAN es `http://<IP de la LAN>:8004/` (o la de `GATEWAY_HOST` si escucha en una dirección concreta; si solo escucha en loopback y no hay otra vía, te dice cómo entrar con un túnel SSH). Para verlas o cambiarlas después: `fogar-gw access` y `fogar-gw access setup`;
   - el token de administración que se pega en él (solo en una terminal), dónde está (`<base>/config.env`, `ADMIN_TOKEN`) y cómo verlo otra vez: `fogar-gw token`;
   - el recordatorio de no compartirlo: quien lo tenga controla el gateway.

   Después te espera hasta que abres el dashboard y pegas el token («Conectado»); Enter sigue sin esperar. El primer cliente se empareja desde Clientes, en el dashboard. También da la dirección `ws://<host>:8004/ws/stream` para quien configure un cliente a mano.

**El token del dashboard:** `fogar-gw token` lo escribe solo en una terminal; en una tubería, un fichero o con `--json` se niega, para que no acabe en un log. No lo pegues en chats ni en capturas. Sin la CLI, está en `config.env` (`ADMIN_TOKEN`), legible solo por tu usuario.

Si lo cortas a medias, `config.env` queda como estaba más lo ya verificado, y volver a ejecutarlo retoma. También sirve para cambiar de proveedores más adelante (en el menú de `fogar-gw`, «Configurar proveedores»): cada paso que `config.env` ya tiene ofrece primero «Dejar como está», que ni verifica ni escribe nada. Necesita una terminal; para scripts, `fogar-gw onboard --answers respuestas.json` no pregunta nada y un fallo de verificación sale con 1 y su motivo. El fichero lleva secretos: tiene que ser tuyo y con permisos 600. Créalo ya privado, para que no haya un momento en que otros puedan leerlo: `(umask 077; cat > respuestas.json)` y pega el JSON (termina con Ctrl+D), o `install -m 600 /dev/null respuestas.json` antes de editarlo. Escribirlo y después hacer `chmod` deja esa ventana abierta.

```json
{
  "openclaw_host": "127.0.0.1", "openclaw_port": 18789, "openclaw_token": "…",
  "stt": {"provider": "off"},
  "tts": {"provider": "fogar", "url": "localhost:8005", "token": "…"},
  "first_client": {"name": "salón", "type": "ha"},
  "access": {"tailscale_serve": true, "public_urls": ["https://fogar.example.com"]}
}
```

`stt` y `tts`: `{"provider": "fogar", "url"?, "token"?}` (por defecto `localhost:9000` / `localhost:8005`, sin clave), `{"provider": "elevenlabs", "api_key"}` (la misma en los dos; si el TTS de `config.env` ya usa ElevenLabs con otra clave, la nueva del STT se verifica también para el TTS antes de guardarla, salvo con `"tts": {"provider": "off"}`) o `{"provider": "off"}`. `first_client` (opcional): `{"name", "type"}` (`ha`, `esp32`, `web`, `app` o `screen`) crea ese cliente si aún no hay ninguno; sin él o con `null`, no crea ninguno. `access` (opcional): `tailscale_serve` (por defecto `false`) activa `tailscale serve` como arriba pero sin preguntar (si hace falta permiso o HTTPS, solo lo explica y sigue: nunca ejecuta sudo) y `public_urls` (por defecto `[]`) añade esas URLs a `PUBLIC_URLS`; una URL que no valga sale con 2. Sin `access`, el paso no se ejecuta. Con `--answers` no espera al dashboard ni escribe el token. Un campo que sobre, u otro que falte, es un error.

#### Emparejar un cliente

En vez de crear el cliente y copiar su clave a mano, el cliente puede darse de alta solo con un código:

```bash
fogar-gw pair --type screen --name tele --agent j-assistant --allowed-agent j-assistant \
  --set tts_voice=ef_dora --set barge_in_enabled:=false
```

1. Enseña un código de 6 cifras (`482 913`), el nombre del gateway y a qué hora caduca (10 minutos).
2. Escríbelo en la pantalla de emparejamiento del cliente. El cliente encuentra el gateway solo (en el mismo equipo o en la tailnet) y llama a `POST /pair`.
3. `fogar-gw pair` espera hasta que se use: «Cliente «tele» emparejado». Ctrl+C anula el código.

Las opciones son las de `clients add` más `--set` para cualquier otro campo (voz, turno…), y deciden cómo se comporta el cliente. El cliente aporta su tipo, lo que sabe reproducir (`output_mode`) y un nombre propuesto; lo que fijes aquí manda. Con `--no-wait` (o `--json`) enseña el código y termina, para scripts.

El código es de un solo uso. Si alguien escribe 5 códigos erróneos seguidos, se anulan todos los abiertos y hay que abrir otro. `fogar-gw clients add` sigue siendo la vía manual.

#### Sin el instalador

Hace falta además `git`, `curl` y [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`). `uv tool` deja `fogar-gw` en `~/.local/bin`: si no está en el PATH, ejecuta `uv tool update-shell` y reabre la terminal, porque `fogar-gw install` pone en la unidad el `fogar-gw` que encuentre en el PATH.

`V` es la última versión de [las releases](https://github.com/fogarhome/fogar-gateway/releases) sin la `v` (p. ej. `V=1.27.0` para la release `v1.27.0`). `constraints.txt` existe desde la v1.26.0 y `fogar-gw migrate`, solo desde la versión que lo incluye.

Cada release publica el wheel `fogar_gateway-<versión>-py3-none-any.whl`, que ya lleva la web compilada (instalar desde `git+` no la trae, porque se compila en la release). Con las mismas versiones que la imagen Docker: cada release publica también `constraints.txt` (exportado de `uv.lock`, sin hashes porque uv no los admite en constraints) y `-c` acepta la URL directamente.

**Con el asistente:**

```bash
V=<versión>   # la última de las releases, sin la v
uv tool install "fogar-gateway @ https://github.com/fogarhome/fogar-gateway/releases/download/v$V/fogar_gateway-$V-py3-none-any.whl" \
  -c https://github.com/fogarhome/fogar-gateway/releases/download/v$V/constraints.txt
loginctl enable-linger $USER        # puede pedir sudo según la distro
fogar-gw onboard
```

**A mano**, paso a paso:

```bash
V=<versión>   # la última de las releases, sin la v
uv tool install "fogar-gateway @ https://github.com/fogarhome/fogar-gateway/releases/download/v$V/fogar_gateway-$V-py3-none-any.whl" \
  -c https://github.com/fogarhome/fogar-gateway/releases/download/v$V/constraints.txt
fogar-gw init               # <base>, config.env con ADMIN_TOKEN aleatorio y la BD
fogar-gw config set OPENCLAW_TOKEN   # prompt oculto; ver la nota de debajo
fogar-gw config set OPENCLAW_HOST <host>   # solo si OpenClaw no está en esta máquina (y OPENCLAW_PORT, por defecto 18789)
loginctl enable-linger $USER        # puede pedir sudo según la distro
fogar-gw install            # crea el servicio de usuario y lo arranca
fogar-gw doctor             # configuración, BD, permisos, servicio y gateway
fogar-gw logs -n 20         # últimas líneas del journal (-f para seguir)
```

- **`OPENCLAW_TOKEN`** es el token del gateway de OpenClaw (el de su configuración). El gateway no lo exige: `install` y `serve` arrancan igual y solo registran un WARNING («OPENCLAW_TOKEN está vacío…»), y `config validate` y `doctor` lo marcan como aviso. Pero sin un token válido OpenClaw rechazará la conexión: `/ready` da 503 y las sesiones se cierran, porque el orquestador es la única dependencia fatal. Los secretos se piden por prompt oculto, ver `config set` en [Admin](#admin-requiere-x-admin-token). `OPENCLAW_HOST` y `OPENCLAW_PORT` se cambian con `fogar-gw config set <AJUSTE> <valor>` (solo los secretos se piden por prompt; sin valor, un ajuste normal sale con 2).
- **Linger y sesión:** `systemctl --user` necesita una sesión con bus de usuario: SSH normal sí; con `su` o `sudo -iu`, no (define antes `XDG_RUNTIME_DIR=/run/user/$(id -u)`).

> **El gateway nunca crea una BD por su cuenta.** Si no la encuentra en `<base>/data/gateway.db`
> (o en la `DATABASE_URL` configurada), se para y explica qué hacer: así no arranca vacío, sin
> clientes, por haberse equivocado de directorio. La crea `fogar-gw init` (o `fogar-gw migrate`,
> que la copia de Docker). La imagen Docker arranca con `fogar-gw serve --create-db`, así que
> `docker compose up` con un `./data` vacío funciona como siempre.

### Servicio (systemd de usuario)

Solo en Linux con systemd. `fogar-gw install` crea `~/.config/systemd/user/fogar-gateway.service` y lo arranca. Exige una config válida, la BD ya creada (`fogar-gw init` o `fogar-gw migrate`; `install` nunca la crea) y el puerto 8004 libre (para el contenedor antes). Si el servicio ya es el nuestro y está activo, reinstala, y si la unidad cambia, lo reinicia. `uninstall` y sobrescribir una unidad distinta con `install` piden confirmación (para scripts, `fogar-gw --yes …`).

- **Ejecutable:** usa el `fogar-gw` del PATH. Rechaza uno dentro de un clon o de una instalación editable, porque la unidad se rompería al moverlo: instala con `uv tool install` o pasa `--exe RUTA`.
- **Linger:** sin `loginctl enable-linger $USER` el servicio se para al cerrar tu sesión; `install` avisa si falta.
- **Registros:** `fogar-gw logs` lee el journal. `--level` y `--session` filtran por nivel mínimo y por sesión o cliente, tanto con `LOG_FORMAT=json` como con texto.

### Actualizar

Las migraciones de la BD se aplican solas al arrancar y no hay vuelta atrás automática de versión: copia `<base>/data/gateway.db` antes. Después, vuelve a ejecutar el instalador: instala la última release encima, reinicia el servicio y pasa `fogar-gw doctor`, sin preguntar (`FOGAR_VERSION=X.Y.Z` para otra versión).

```bash
curl -fsSL https://github.com/fogarhome/fogar-gateway/releases/latest/download/install.sh | sh
```

A mano: instala la nueva versión sobre la anterior y reinicia (pregunta; en scripts, `fogar-gw --yes restart`).

```bash
V=<versión>   # la nueva, sin la v
uv tool install --reinstall "fogar-gateway @ https://github.com/fogarhome/fogar-gateway/releases/download/v$V/fogar_gateway-$V-py3-none-any.whl" \
  -c https://github.com/fogarhome/fogar-gateway/releases/download/v$V/constraints.txt
fogar-gw restart
fogar-gw version           # versión de la CLI y del gateway en marcha
```

### Desinstalar

```bash
fogar-gw uninstall                # quita el servicio (pregunta); no toca los datos
uv tool uninstall fogar-gateway   # quita el programa
```

Los datos (`config.env`, `data/gateway.db`) quedan en `<base>`; bórralos a mano si ya no los quieres.

### Pasar de Docker a nativo

`fogar-gw migrate --from-docker <carpeta de compose>` copia `.env` y `data/gateway.db` de una instalación Docker a `<base>`. Solo vale para una instalación Docker de la v2.0.0 o posterior: las versiones 1.x no se migran, se instala desde cero. Procedimiento, con la ventana de parada y la vuelta atrás:

1. **Misma versión a los dos lados.** Despliega la versión vN en Docker e instala la vN nativa **sin arrancarla**, con el instalador y `--no-onboard`:
   ```bash
   curl -fsSL https://github.com/fogarhome/fogar-gateway/releases/latest/download/install.sh | FOGAR_VERSION=<N> sh -s -- --no-onboard
   ```
   y **nada más** (ni `fogar-gw init`, ni `fogar-gw onboard`, ni `fogar-gw install`: `migrate` se niega si `<base>` ya tiene `config.env`, BD o restos de SQLite). El instalador acaba con «Configúralo con `fogar-gw onboard` o, si vienes de Docker, …»: tú sigues la vía de Docker, con `migrate` en el paso 3 e `install` en el 5. `<N>` es la versión de Docker sin la `v`: `fogar-gw version` da la nativa y `docker exec fogar_gateway fogar-gw version` la de Docker. El instalador ya deja linger activado.
2. **Ventana sin sesiones.** `docker exec fogar_gateway fogar-gw status` debe mostrar 0 sesiones.
3. **Parar, copiar y migrar.** `docker compose stop` (sin `down`), copia de seguridad de la BD y migra (usa un usuario que pueda leer `<carpeta>/data` y su `gateway.lock`):
   ```bash
   cp -a <carpeta de compose>/data/gateway.db* <destino de la copia>/
   fogar-gw migrate --from-docker <carpeta de compose>
   ```
4. **Revisar la config migrada, antes de instalar.** `fogar-gw config show` y `fogar-gw doctor`: los nombres que solo resuelven en la red de Docker (nombres de servicios de compose, `host.docker.internal`) hay que cambiarlos con `fogar-gw config set <AJUSTE> <valor>` (p. ej. `fogar-gw config set OPENCLAW_HOST <host>`).
5. **Instalar el servicio:** `fogar-gw install`, que comprueba que el 8004 está libre.
   Si `GATEWAY_HOST` es una IP concreta de la LAN, `install` espera a `/healthz` en la URL de `--url` (por defecto `http://127.0.0.1:8004`) y daría error por tiempo: usa `fogar-gw --url http://<IP>:8004 install`. La comprobación de puerto libre sí mira solo `127.0.0.1`.
6. **Verificar.** `curl http://127.0.0.1:8004/ready`, una sesión de voz o texto, `/v1` con y sin clave, los mismos clientes (`fogar-gw clients list`), `fogar-gw doctor` y `fogar-gw logs -n 20`.
7. **Vuelta atrás.** `fogar-gw stop` y `systemctl --user disable fogar-gateway.service` (si no, tras un reinicio de la máquina arrancan los dos y se pelean por el 8004), y después `docker compose up -d`. Para volver a nativo: `docker compose stop` y `fogar-gw install` (con la unidad igual no la toca: la habilita y la arranca). Los cambios de clientes hechos en un lado no están en la BD del otro.
8. **Retirar el contenedor**, más tarde: `docker compose down`, sin borrar la imagen ni la carpeta.

Qué hace `migrate` (`src/fogar_gateway/cli/migrate.py`):

- Copia la BD con la API de backup de sqlite3, comprueba `integrity_check` y que los clientes coinciden; **nunca escribe el origen** (lo abre en solo lectura). Si algo falla, no queda nada a medias, salvo un corte brusco (SIGKILL, corte de luz) entre la copia de la BD y la de `config.env`: entonces borra a mano lo creado en `<base>` y repite.
- Exige la misma revisión de Alembic a los dos lados; si no, se niega y dice qué desplegar.
- Se niega si el contenedor sigue en marcha mientras el gateway de Docker tenga el candado sobre `data/gateway.lock` (por defecto sí; no con `SINGLE_PROCESS_LOCK=false`), si `<base>` ya tiene `config.env` o BD (o restos `-journal`/`-wal`/`-shm`), o si el `DATABASE_URL` del `.env` no apunta a `/app/data/gateway.db`.
- Del `.env` quita `DATABASE_URL`, `GATEWAY_UID` y `GATEWAY_GID`, y añade `LOG_FORMAT=json` si falta (la imagen lo fijaba). Una clave del `.env` que no sea un ajuste del gateway hace que se niegue ("La configuración migrada no es válida"): quítala antes y repite.
- Crea `<base>` y `data/` con permisos 700 y `config.env` y la BD con 600. No migra los datos más allá de `data/gateway.db`: si montabas más cosas en el volumen, cópialas a mano.

### Desarrollo en la misma máquina que producción

Usa siempre `./.dev` como base (`--home ./.dev` o `FOGAR_GATEWAY_HOME=./.dev`): sin él, la CLI y el gateway comparten `~/.fogar-gateway`, o sea, la BD de producción. Además, `serve` usa siempre el puerto 8004 y la CLI apunta por defecto a `http://127.0.0.1:8004`: en la misma máquina el gateway de desarrollo choca con el de producción, o `clients list` acaba hablando con producción. Por eso el ejemplo de abajo usa el puerto 8014 (el 8005 es el `TTS_WS_URL` por defecto de fogar-speaker) con `uvicorn` y apunta la CLI con `FOGAR_GW_URL` (o `--url`).

```bash
git clone https://github.com/fogarhome/fogar-gateway && cd fogar-gateway
uv sync --extra dev   # crea .venv desde uv.lock (o `pip install -e .[dev]`, sin versiones fijadas)
source .venv/bin/activate   # sin esto (o `uv run fogar-gw …`), `fogar-gw` es el de producción
export FOGAR_GATEWAY_HOME=./.dev FOGAR_GW_URL=http://127.0.0.1:8014
fogar-gw init                # una vez
uvicorn fogar_gateway.main:app --port 8014 --reload   # `fogar-gw serve` fija el 8004
fogar-gw clients list        # habla con el 8014
```

### Docker (alternativa)

```bash
docker compose up
```

> El contenedor corre como no-root. Antes del primer `docker compose up`
> define `GATEWAY_UID`/`GATEWAY_GID` en `.env` con el dueño de `./data`
> (`echo "GATEWAY_UID=$(id -u)" >> .env && echo "GATEWAY_GID=$(id -g)" >> .env`).
> Si `./data` viene de un despliegue anterior como root: `sudo chown -R "$(id -u):$(id -g)" data`.
> Detalles en `docker-compose.yml`. Para pasar a nativo, ver [Pasar de Docker a nativo](#pasar-de-docker-a-nativo).

---

## Configuración

Variables de entorno o, si no, de `<base>/config.env` (instalación nativa). En Docker van en el `.env` junto a `docker-compose.yml`, que las pasa como variables. Formato `host:port` sin protocolo para todos los URLs. Esta tabla es un resumen: la referencia completa, con todos los plazos y sus defaults, es [`.env.sample`](.env.sample).

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///data/gateway.db` | Base de datos SQLite local (identidad y configuración de clientes). Una ruta relativa cuelga de `<base>` |
| `GATEWAY_HOST` | `0.0.0.0` | Host de escucha de `fogar-gw serve` (`127.0.0.1` detrás de un proxy en el mismo host) |
| `GATEWAY_NAME` | — | Nombre con el que se anuncia a los clientes (`/.well-known/fogar-gateway`). Vacío = nombre del equipo; en Docker conviene fijarlo |
| `PUBLIC_URLS` | — | URLs `https://` propias por las que se llega al gateway (CSV, sin ruta, parámetros ni usuario), p. ej. `https://fogar.example.com`. Vacío = ninguna |
| `TRANSCRIBER_WS_URL` | `localhost:9000` | fogar-transcriber |
| `TTS_WS_URL` | `localhost:8005` | fogar-speaker |
| `TTS_TOKEN` | — | Token de auth para fogar-speaker; vacío si el TTS no exige clave |
| `OPENCLAW_HOST` | `127.0.0.1` | Host de OpenClaw |
| `OPENCLAW_PORT` | `18789` | Puerto de OpenClaw |
| `OPENCLAW_TOKEN` | — | Token de OpenClaw. Sin él el gateway arranca con un aviso, pero OpenClaw rechaza la conexión (`/ready` 503) |
| `ADMIN_TOKEN` | — | Token para endpoints `/admin/*`; vacío deshabilita el admin API |
| `ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial en reconexión del orquestador (s) |
| `ORCHESTRATOR_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo en reconexión del orquestador (s) (el gateway reintenta sin parar, sin tope de duración) |
| `SINGLE_PROCESS_LOCK` | `true` | `flock` sobre `gateway.lock` junto a la BD: un segundo proceso (p. ej. `--workers 2`) no arranca. Multi-worker no está soportado |
| `SESSION_REVALIDATE_INTERVAL_S` | `10.0` | Cada sesión WS se revalida contra la BD; cliente borrado/desactivado/clave rotada → cierre 1008 `revoked` |
| `TRANSCRIBER_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial en reconexión del transcriptor (s) |
| `TRANSCRIBER_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo en reconexión del transcriptor (s) |
| `TRANSCRIBER_RECONNECT_MAX_DURATION` | `300.0` | Tiempo hasta DEGRADED del transcriptor (s) |
| `TTS_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial del breaker de TTS (s) |
| `TTS_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo del breaker de TTS (s) — sin estado DEGRADED, cada turno elegible reintenta |

### Proveedores de voz (STT/TTS)

El STT y el TTS pasan por una capa de proveedores (`src/fogar_gateway/services/speech/`). Los clientes no notan la diferencia: mismos formatos de audio y mismos mensajes.

| Variable | Default | Descripción |
|----------|---------|-------------|
| `STT_PROVIDER` | `fogar` | `fogar` (fogar-transcriber), `elevenlabs` (Scribe v2 Realtime) u `off` (sin transcripción: el audio de los clientes se descarta y solo entra texto) |
| `TTS_PROVIDER` | `fogar` | `fogar` (fogar-speaker), `elevenlabs` (stream-input, `eleven_flash_v2_5`) u `off` (sin respuestas en audio: solo en texto) |
| `ELEVENLABS_API_KEY` | — | Obligatoria si algún proveedor es `elevenlabs` (permisos Text to Speech y Speech to Text) |
| `ELEVENLABS_TTS_DEFAULT_VOICE` | `gc61tTk93h3LBXPxew2V` | Voz por defecto; las voces de clientes que no son de ElevenLabs (p. ej. `af_heart`) caen en ella o se traducen con `TTS_VOICE_MAP` |
| `STT_AUDIO_GATE` | `energy` | Puerta de energía del STT de ElevenLabs (solo se envía audio mientras se habla); `off` la quita. fogar no la usa |
| `STT_GATE_RMS_DBFS` | `-45` | Umbral de la puerta de energía |

`ELEVENLABS_BASE_URL` y `ELEVENLABS_TTS_MODEL` están en `.env.sample`; el resto de ajustes de ElevenLabs son constantes del adaptador (`src/fogar_gateway/services/speech/elevenlabs/`). El proveedor se elige con `STT_PROVIDER`/`TTS_PROVIDER` al arrancar; cambiarlo exige reiniciar. Con el plan Creator, ElevenLabs conserva el audio y las transcripciones (la retención cero es solo enterprise).

`silence_timeout_s` y `max_silence_turns` (umbral del watchdog de silencio) son campos de configuración **por cliente** en la base de datos, no variables de entorno globales — ver `ClientRecord` en `src/fogar_gateway/db/models.py`.

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
   label `fogar-e2e`, contra este repo.
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

El gateway instancia un `FogarBridge` por cada sesión WebSocket. Los tres servicios downstream (OpenClaw, Transcriber, TTS) tienen reconexión automática con backoff exponencial, unificada en `services/reconnection.py` (estados compartidos CONNECTED / RECONNECTING / DEGRADED). OpenClaw y los proveedores de voz son singletons de proceso; con el proveedor `fogar`, cada sesión de audio abre su propio `ReconnectingTranscriberClient` y cada turno su propio `TTSClient`.

```
src/fogar_gateway/
├── paths.py                 directorio base, config.env y ruta de la BD (compartido con la CLI)
├── access.py                vías para llegar al gateway: LAN, Tailscale, PUBLIC_URLS; verificación y recomendada
├── alembic.ini, migrations/ migraciones de Alembic, dentro del paquete instalado
├── web/                     el panel compilado (lo mete el build en el wheel; no está en git)
├── api/
│   ├── routes.py            WebSocket /ws/stream — handshake, ready, ciclo de vida del bridge
│   ├── pairing_routes.py    /.well-known/fogar-gateway, POST /pair y /admin/pairing
│   ├── admin_routes.py      /admin/* — clientes, sesiones, servicios, voz, vías de acceso y estado en vivo (SSE)
│   ├── openai_routes.py     /v1/models, /v1/chat/completions
│   ├── web_routes.py        el panel en / (caché, CSP y vuelta a index.html)
│   ├── health_routes.py     /healthz, /ready
│   └── deps.py              get_admin_auth — dependencia de auth para admin
├── core/
│   ├── config.py            Settings (pydantic-settings, <base>/config.env)
│   ├── agent_policy.py      resolve_agent() — cascada y política de agentes
│   ├── network.py           resolve_client_ip() / origen de confianza de /v1
│   ├── startup_checks.py    lock de proceso único y comprobaciones de arranque
│   ├── logging.py           formatos de log y fingerprint_key()
│   └── session_key.py       make_session_key() — formato canónico de session key
├── db/
│   ├── models.py            ClientRecord (SQLModel) — identidad y config por cliente
│   └── database.py          get_engine() / run_migrations() / get_db_session()
├── cli/                     fogar-gw — init, serve, menú, onboard (el asistente; verify.py comprueba cada
│                            proveedor contra el servicio real), access, token, pair, clients, doctor, config,
│                            status, version, servicio systemd (install/start/stop/restart), logs y migrate
└── services/
    ├── bridge.py            FogarBridge — coordinador de sesión WS
    ├── pairing.py           PairingStore — códigos de emparejamiento en memoria
    ├── db_client.py         DbClient — lee ClientRecord de SQLite local (sin caché)
    ├── outbound.py          QueuedSender — salida serializada hacia cada cliente
    ├── pacing.py            AudioPacer — el audio TTS a 1× con un pequeño adelanto
    ├── reconnection.py      ConnectionState / ServiceStatus / to_wire_state() — compartido por los 3 wrappers
    ├── teardown.py          Teardown — cierre ordenado sin perder errores ni cancelaciones
    ├── transcriber_client.py       WebSocket → fogar-transcriber, protocolo puro (sin reconexión)
    ├── transcriber_reconnecting.py ReconnectingTranscriberClient — uno por sesión de audio, backoff en background
    ├── tts_client.py                WebSocket → fogar-speaker, protocolo puro, creado por turno
    ├── tts_reconnecting.py          ReconnectingTTSClient — singleton, backoff perezoso por turno
    ├── orchestration.py     call_orchestrator() — helper compartido WS+HTTP
    ├── pipeline_tracker.py  PipelineTracker — latencias por turno
    ├── session_registry.py  SessionRegistry — observabilidad de sesiones
    ├── speech/              Proveedores STT/TTS: ports.py, factory.py, fogar.py, elevenlabs/, off.py (voz desactivada)
    └── openclaw/
        ├── client.py        OpenClawClient — WebSocket v4, multiplexado
        ├── reconnecting.py  ReconnectingOpenClawClient — backoff + DEGRADED
        ├── dispatcher.py    FrameDispatcher — enruta frames a turn/client registry
        ├── registry.py      TurnRegistry + ClientRegistry (+ broadcast_status a todas las sesiones)
        └── models.py        GatewayInfo
```

En la raíz del repo, `install.sh` es el instalador nativo (se publica como asset de cada release; el job `installer-smoke` del CI lo prueba).

## Licencia

MIT. Ver [LICENSE](LICENSE).
