# Protocolo de cliente — fogar-gateway

Guía completa para implementar clientes que se conecten a fogar-gateway. Cubre el ciclo de vida de la sesión WebSocket, todos los mensajes posibles en ambas direcciones y el formato de audio binario.

> **¿Integras Home Assistant u otro cliente OpenAI-compatible?** Eso va por REST (`/v1/*`), no por este protocolo WebSocket. Su modelo de autenticación (origen de confianza o `Authorization: Bearer <client_key>`) está en el [README](../README.md#openai-compatible-v1-para-home-assistant).

---

## Índice

0. [Descubrir el gateway y emparejarse](#0-descubrir-el-gateway-y-emparejarse)
1. [Conexión y handshake](#1-conexión-y-handshake)
2. [Mensaje ready](#2-mensaje-ready)
3. [Enviar audio de micrófono](#3-enviar-audio-de-micrófono)
4. [Enviar texto](#4-enviar-texto)
5. [Recibir tokens de texto](#5-recibir-tokens-de-texto)
6. [Recibir audio TTS](#6-recibir-audio-tts)
7. [Barge-in e interrupciones](#7-barge-in-e-interrupciones)
8. [Estado de servicios](#8-estado-de-servicios)
9. [Mensajes de error](#9-mensajes-de-error)
10. [Turns iniciados por el agente (push)](#10-turns-iniciados-por-el-agente-push)
11. [Modos de operación](#11-modos-de-operación)
12. [Referencia completa de mensajes](#12-referencia-completa-de-mensajes)
13. [Timeouts y cierre de sesión](#13-timeouts-y-cierre-de-sesión)

---

## 0. Descubrir el gateway y emparejarse

Antes de la sesión, tu cliente necesita dos cosas: **dónde está el gateway** y **su `client_key`**. Las dos pueden venir de la configuración (un administrador creó el cliente con `fogar-gw clients add` y te pasó la clave), pero lo cómodo es que el cliente las consiga solo. Esta sección es lo que tiene que implementar.

### Encontrar el gateway

Un gateway se identifica en un endpoint público, sin autenticación:

```http
GET http://<host>:8004/.well-known/fogar-gateway
```

```json
{"service": "fogar-gateway", "version": "1.29.0", "name": "green-house", "instance_id": "3f9a1c2e7b5d4a60"}
```

Es un Fogar si responde 200 con `"service": "fogar-gateway"`. No basta con `/healthz`: su `{"status": "ok"}` lo devolvería cualquier servicio. `name` es el nombre que enseñar al usuario (`GATEWAY_NAME`, o el del equipo). `instance_id` cambia en cada arranque del proceso y sirve para comprobar que una dirección llega a este gateway; no es un secreto.

Una dirección es `host` o `host:puerto` (puerto 8004 por defecto), sin protocolo. En la LAN y en la tailnet se habla en claro (`http://`, `ws://`); detrás de un proxy con TLS, `https://` y `wss://`.

Si ya estás emparejado, usa la dirección que guardaste junto a la clave: la clave solo vale en el gateway que la emitió. Si no, busca en este orden y quédate con el primero que responda, con un plazo corto por intento (1–2 s):

1. **La dirección configurada**, si el usuario puso una.
2. **El mismo equipo:** `127.0.0.1:8004`. Vale igual para el gateway nativo y para Docker con el puerto publicado. Si tu cliente admite otro puerto por configuración, úsalo; no barras puertos. Un gateway que escucha solo en una IP de la LAN (`GATEWAY_HOST`) no responde aquí: ese caso cae en el paso 4.
3. **La tailnet, si hay Tailscale:** `tailscale status --json` lista los equipos (`Peer[*]`, con `Online`, `DNSName` y `TailscaleIPs`). Pregunta en `:8004` a cada equipo en línea, por su `DNSName` sin el punto final, en paralelo y con un plazo total de unos 3 s. Si `tailscale` no está o no tiene sesión, salta este paso. Si responde más de uno, deja elegir al usuario por `name`.
4. **Si no aparece ninguno,** pide la dirección a mano y compruébala igual, con `/.well-known/fogar-gateway`.

No escanees redes ni rangos de IP.

### Emparejarse

El administrador abre un código en el gateway con `fogar-gw pair` (ver el [README](../README.md#emparejar-un-cliente)). El gateway lo enseña: 6 cifras, de un solo uso y válido 10 minutos. En la configuración del código el administrador ya ha fijado cómo se comportará tu cliente: agente, voz, barge-in…

Tu cliente necesita **una pantalla de emparejamiento donde la persona solo escribe el código**. Todo lo demás lo pone el cliente:

```http
POST http://<host>:8004/pair
Content-Type: application/json

{"code": "482913", "name": "Tele del salón", "client_type": "screen", "output_mode": ["audio", "text", "status"]}
```

| Campo | Requerido | Quién lo pone |
|-------|-----------|---------------|
| `code` | ✓ | La persona. El gateway ignora los espacios (`"482 913"`); comprueba en el cliente que quedan 6 cifras antes de enviarlo, para no gastar un intento en una errata |
| `name` | — | El cliente propone uno (el nombre del dispositivo o el que elija el usuario, si le dejas). Si el administrador fijó otro, gana el suyo; si nadie da uno, el gateway pone `<tipo>-xxxx` |
| `client_type` | — | Fijo en tu cliente: `ha`, `esp32`, `web`, `app` o `screen` |
| `output_mode` | — | Fijo en tu cliente: lo que sabe reproducir (`"audio"`, `"text"`, `"status"`, como en §1). Se guarda en la ficha del cliente para el administrador; en cada sesión manda el `output_mode` del handshake |

Regla: **el cliente dice qué es** (tipo y formatos) **y el administrador decide cómo se comporta**. Lo que fijó el administrador al abrir el código manda sobre lo que mandes. El handshake de cada sesión (§1) sigue llevando `input_mode` y `output_mode`; no mandes `agent` salvo que el usuario elija uno, para que valga el que fijó el administrador.

| Respuesta | Qué significa |
|-----------|---------------|
| `201 {"client_id", "name", "client_key", "gateway": {"name", "version"}}` | Emparejado. **`client_key` solo llega aquí, una vez:** guárdala ya junto a la dirección del gateway, con permisos 0600 si va a un fichero, y nunca la escribas en un log. `name` es el nombre con el que quedó dado de alta (puede no ser el que propusiste) |
| `400 {"detail": "invalid or expired code"}` | Código desconocido, caducado, ya usado o mal escrito (siempre el mismo mensaje). Deja corregirlo |
| `422` | `code` no es un texto de hasta 32 caracteres (mándalo como texto, no como número), o `name` (1–200 caracteres), `client_type` u `output_mode` no válidos: es un fallo de tu cliente, no del usuario. No cuenta como intento |
| `5xx` o sin respuesta | Si el gateway falló al crear el cliente, el código sigue abierto: reintenta con el mismo código. Si se cortó la conexión después del `201`, el código ya está gastado y la clave perdida: pide otro código |

Después abre la sesión normal (§1) con esa `client_key`.

**Intentos fallidos:** mientras haya códigos abiertos, cada `400` cuenta (un código caducado o ya usado también), sea de quien sea, y al quinto seguido se anulan todos los abiertos (un canje correcto pone la cuenta a 0); el administrador tiene que abrir otro. Es la defensa contra quien intente adivinar, porque `/pair` puede estar expuesto a internet a través de un proxy. El cliente no puede saber cuántos intentos quedan: si tras un `400` el usuario está seguro del código, dile que pida uno nuevo.

### Cuando la clave deja de valer

Si al abrir la sesión el gateway cierra con 1008 `"Clave de cliente invalida o inactiva."`, o una sesión abierta se cierra con 1008 `"revoked"` (§13), el administrador ha borrado, desactivado o rotado tu cliente. Estos dos textos son estables: compáralos tal cual. Borra la clave guardada y vuelve a la pantalla de emparejamiento. Cualquier otro cierre 1008 (handshake mal formado, agente no permitido) es un fallo de tu cliente y no invalida la clave. No reintentes en bucle con la misma clave.

---

## 1. Conexión y handshake

**Endpoint WebSocket:** `ws://<host>:8004/ws/stream`

El **primer mensaje** que envíes DEBE ser el handshake. Es el único mensaje sin campo `type`.

```json
{
  "client_key": "tu-api-key",
  "input_mode": "audio",
  "output_mode": ["audio", "text", "status"],
  "agent": "main"
}
```

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `client_key` | string | ✓ | Clave de autenticación del cliente |
| `input_mode` | `"audio"` \| `"text"` | ✓ | Cómo enviará datos este cliente |
| `output_mode` | array | ✓ | Qué quiere recibir: `"text"` (mensajes `token`), `"audio"` (frames TTS), `"status"` (mensajes `pipeline_event`, §12). Los `status` de servicios, `error`, `transcription*` y el control de turnos llegan siempre |
| `agent` | string | — | Agente OpenClaw a usar; omitir para usar el agente por defecto (ver "Selección de agente" abajo) |

### Selección de agente

El agente efectivo de la sesión no es simplemente "el que pediste o el global": el gateway resuelve una cascada server-side (`agent` del handshake si no está vacío → `default_agent` configurado por el admin para tu `client_key` → default global de OpenClaw → `"main"`) y aplica dos comprobaciones de política antes de aceptar la conexión: si tu cliente tiene una lista `allowed_agents`, el agente resultante debe estar en ella (una lista vacía lo deniega todo); y si pediste el agente explícitamente, debe existir en el roster de OpenClaw (`src/fogar_gateway/core/agent_policy.py`). Como cliente solo necesitas saber dos cosas:

1. Si omites `agent` en el handshake, el que acabes usando puede venir de una configuración por-cliente que un admin te haya asignado (no siempre el default global) — comprueba siempre `ready.agent`, no asumas cuál será.
2. Pedir explícitamente un `agent` puede fallar de dos formas distintas (ver tabla siguiente): que ese agente no esté permitido para tu cliente, o que no exista en OpenClaw.

### Errores de handshake (el servidor cierra la conexión)

| Situación | Código WS | Motivo |
|-----------|-----------|--------|
| No se recibe el handshake JSON en los primeros `HANDSHAKE_TIMEOUT_S` (5s por defecto) | 1008 | `"Handshake timeout"` |
| El handshake supera `HANDSHAKE_MAX_BYTES` (4096 caracteres) | 1008 | `"Handshake too large"` |
| JSON inválido o campos incorrectos | 1008 | `"Handshake invalido. Se esperaba JSON de configuración."` |
| El primer mensaje es binario, no texto | 1008 | `"Handshake invalido. Se esperaba texto."` |
| `client_key` inválida o cliente inactivo | 1008 | `"Clave de cliente invalida o inactiva."` |
| Agente solicitado no está en la lista permitida para este cliente (`allowed_agents`) | 1008 | `"Agent '{name}' not permitted for this client."` |
| Agente solicitado no existe en OpenClaw | 1008 | `"Agent '{name}' not available."` |
| Fallo al abrir los servicios internos de la sesión | 1011 | `"Problema estableciendo microservicios internos del hub."` |
| Orquestador no disponible tras health check | 1011 | `"Servicio crítico no disponible."` |

> **El orquestador (OpenClaw) es el único servicio que puede cerrar la conexión en el handshake.** Transcriber y TTS ya **no** son críticos: si cualquiera de los dos no responde al arrancar la sesión, el gateway la abre igualmente en modo degradado y te lo notifica vía `status` (ver §8) — nunca cierra el WebSocket por esto. Antes de la versión que introdujo la reconexión automática (issue #46), un fallo de Transcriber en este punto sí cerraba la conexión con 1011; ya no es el caso.
>
> Estos mensajes `status` de arranque pueden llegar **antes** del `ready` (el health check se ejecuta justo antes de enviarlo) — no asumas que `ready` es el primer mensaje posible tras el handshake, solo que es el primero que confirma que la sesión quedó establecida.

---

## 2. Mensaje ready

Si el handshake es válido y todos los servicios críticos responden, el gateway envía `ready` como el mensaje que confirma la sesión; pueden llegar `status` antes (ver nota en §1 y §5):

```json
{
  "type": "ready",
  "session_id": "hab_sito:1751289600000",
  "agent": "main",
  "input_mode": "audio",
  "output_mode": ["audio", "text"],
  "requested_capabilities": {
    "barge_in": true,
    "tts": true,
    "transcriber": true
  },
  "live_capabilities": {
    "barge_in": true,
    "tts": true,
    "transcriber": true
  }
}
```

| Campo | Descripción |
|-------|-------------|
| `session_id` | Identificador único de esta sesión (`client_id:timestamp_ms`) |
| `agent` | Agente OpenClaw activo (el solicitado o el por defecto) |
| `input_mode` | Modo de entrada confirmado |
| `output_mode` | Modos de salida activos |
| `requested_capabilities.barge_in` | Si el barge-in está habilitado para este cliente |
| `requested_capabilities.tts` | Si el cliente pidió salida de audio (`"audio"` en `output_mode`) |
| `requested_capabilities.transcriber` | Si el cliente pidió modo audio (`input_mode == "audio"`) |
| `live_capabilities.barge_in` | Si el barge-in está operativo (requiere barge-in habilitado, transcriber activo y un proveedor STT con parciales) |
| `live_capabilities.tts` | Si TTS responde en este momento |
| `live_capabilities.transcriber` | Si el transcriptor está conectado (`input_mode == "audio"`) |

Espera este mensaje antes de enviar audio o texto. Si no llega, la conexión fue cerrada por un error de handshake.

> **Voz desactivada.** Si el gateway tiene la voz en `off` (`STT_PROVIDER`/`TTS_PROVIDER`), se comporta como con ese servicio caído, y solo para lo que pidió la sesión: antes de `ready` llega `status` con `state: "unavailable"` para `transcriber` si `input_mode` es `"audio"`, y para `tts` si `output_mode` incluye `"audio"`; una sesión solo de texto no recibe ninguno. `live_capabilities.transcriber`/`tts` (y `barge_in`) valen `false`. La sesión sigue abierta en texto: `send` funciona, el audio que mandes se descarta y no hay avisos de recuperación.

> **Incompatibilidad de protocolo — #114 (v1.15.0)**
> `ready.capabilities` fue reemplazado por `ready.requested_capabilities` + `ready.live_capabilities`.
> `requested_capabilities` refleja lo que el cliente pidió en el handshake.
> `live_capabilities` refleja el resultado del health check en el momento de la conexión.
> Clientes que aún lean `capabilities` dejarán de encontrar el campo — deben actualizarse.

## 3. Enviar audio de micrófono

Requiere `input_mode: "audio"` en el handshake.

Envía frames binarios de audio crudo con este formato exacto:

| Parámetro | Valor |
|-----------|-------|
| Formato | PCM Float32 little-endian |
| Sample rate | 16 000 Hz |
| Canales | 1 (mono) |
| Header | ninguno — solo los bytes de audio |

### Flujo de voz completo

```
Cliente                          Gateway
  │─── [PCM Float32] ──────────►│
  │─── [PCM Float32] ──────────►│
  │◄── {"type":"transcription_partial","text":"enciende la..."} ─│
  │─── [PCM Float32] ──────────►│
  │─── {"type":"end"} ─────────►│   ← usuario suelta el micrófono
  │◄── {"type":"transcription","text":"enciende la luz"} ────────│
  │                              │   ← cliente muestra texto para revisión
  │─── {"type":"send","text":"enciende la luz"} ───────────────►│
  │◄── {"type":"turn_start","turn_id":"t-1","turn_seq":1} ───────│
  │◄── {"type":"token","turn_id":"t-1","text":"Vale,"} ──────────│
  │◄── [0xA1][0x00][0x01][PCM16...] ────────────────────────────│
  │◄── {"type":"token","turn_id":"t-1","text":" encendida."} ────│
  │◄── [0xA1][0x00][0x01][PCM16...] ────────────────────────────│
  │◄── {"type":"turn_end","turn_id":"t-1"} ──────────────────────│
```

### Señal de fin de audio

Envía `{"type": "end"}` cuando el usuario termine de hablar:

```json
{ "type": "end" }
```

Úsalo cuando el usuario suelta el botón de micrófono, detectas silencio prolongado en cliente, o el VAD local decide que la frase terminó. Tras `end`, el transcriptor entrega la transcripción final y el cliente espera para enviarla con `send`.

---

## 4. Enviar texto

### Mensaje send (modo canónico)

Envía texto al orquestador — ya sea una transcripción confirmada o texto escrito directamente:

```json
{ "type": "send", "text": "¿Cuál es la capital de Francia?" }
```

El campo `text` puede diferir de la transcripción original si el usuario la editó.

### Cancelar turno activo

Si el usuario cancela la respuesta en curso sin lanzar una nueva:

```json
{ "type": "cancel" }
```

También corta un push que esté sonando (§10): recibes su `turn_end`.

---

## 5. Recibir tokens de texto

Requiere `"text"` en `output_mode`.

Cada turno consiste en tres mensajes en este orden:

### `turn_start` — inicio de turno

```json
{ "type": "turn_start", "turn_id": "t-1", "turn_seq": 1 }
```

| Campo | Descripción |
|-------|-------------|
| `turn_id` | Identificador del turno (`t-{N}`, secuencial por sesión desde 1) |
| `turn_seq` | N módulo 65 536 (uint16, no monótono tras el desbordamiento; compáralo por igualdad, no por orden) — es el mismo valor que lleva el header de los frames de audio |

### `token` — texto en streaming

```json
{ "type": "token", "turn_id": "t-1", "text": "La capital" }
{ "type": "token", "turn_id": "t-1", "text": " de Francia" }
{ "type": "token", "turn_id": "t-1", "text": " es París." }
```

Los tokens llegan en orden; concaténalos para reconstruir la respuesta completa. El campo `turn_id` permite asociar tokens con su `turn_start` aunque lleguen entrelazados con frames de audio.

### `turn_end` — fin de turno

```json
{ "type": "turn_end", "turn_id": "t-1" }
```

Señala que el turno terminó. **Todo turno con `turn_start` recibe exactamente un `turn_end`** — también si falla (tras un `error`), si se cancela (`cancel`, barge-in, transcripción final, un `send` que lo sustituye) o si el cierre de sesión lo corta. Después del `turn_end` no se envía más audio de ese turno; lo que sigue es la reproducción del audio que ya recibiste (como mucho `AUDIO_LEAD_S`, unos 2 s, por delante del tiempo real).

### `tool_call` — uso de herramientas del agente (opt-in)

Solo se envía si el cliente tiene el flag `tool_calls_enabled` activado (por defecto `False`, gestionable vía Admin API — ver el [README](../README.md#admin-requiere-x-admin-token)). Cuando el agente invoca una herramienta durante el turno, llegan dos mensajes intercalados con los `token`:

```json
{ "type": "tool_call", "turn_id": "t-1", "phase": "start", "name": "exec", "tool_call_id": "call-1", "args": {"command": "ls"}, "result": null, "is_error": null }
{ "type": "tool_call", "turn_id": "t-1", "phase": "result", "name": "exec", "tool_call_id": "call-1", "args": null, "result": "file.txt", "is_error": false }
```

| Campo | Descripción |
|-------|-------------|
| `turn_id` | Turno al que pertenece (mismo `turn_id` que su `turn_start`) |
| `phase` | `"start"` (la herramienta empieza a ejecutarse) o `"result"` (resultado disponible) |
| `name` | Nombre de la herramienta |
| `tool_call_id` | Identificador único de esta invocación — correlaciona `start` con su `result` |
| `args` | Argumentos pasados a la herramienta (solo en `phase="start"`) |
| `result` | Resultado en texto plano (solo en `phase="result"`) |
| `is_error` | Si la herramienta falló (solo en `phase="result"`) |

No hay un `phase="update"` — OpenClaw emite resultados parciales de streaming que el gateway descarta deliberadamente; solo se reenvían inicio y resultado final. También llegan durante turnos iniciados por el agente (push, ver §10).

### Pseudocódigo de recepción

```python
current_buffer = {}

async for msg in ws:
    if isinstance(msg, str):
        data = json.loads(msg)
        match data["type"]:
            case "ready":
                session_id = data["session_id"]
                agent = data["agent"]
                requested = data["requested_capabilities"]
                live = data["live_capabilities"]

            case "turn_start":
                turn_id = data["turn_id"]
                current_buffer[turn_id] = ""

            case "token":
                turn_id = data["turn_id"]
                current_buffer[turn_id] += data["text"]
                render_streaming(data["text"])

            case "turn_end":
                turn_id = data["turn_id"]
                on_turn_complete(current_buffer.pop(turn_id, ""))

            case "tool_call":
                # Solo llega si tool_calls_enabled=True para este cliente
                handle_tool_call(data["turn_id"], data["phase"], data["name"], data.get("args"), data.get("result"))

            case "transcription_partial":
                show_live_transcription(data["text"])

            case "transcription":
                show_final_transcription_for_review(data["text"])

            case "interrupted":
                clear_current_response_ui()

            case "status":
                handle_service_status(data["service"], data["state"])

            case "error":
                handle_error(data["code"], data["message"], data["fatal"])

    elif isinstance(msg, bytes):
        play_audio_frame(msg)
```

### Orden total de mensajes

El gateway entrega todos los mensajes de una sesión en un único **orden total** — el mismo orden en que el gateway los encola internamente (issue #130 S1). Esto garantiza que nunca hay interleaving de mensajes parciales: un `token` completo llega antes del siguiente, un `turn_end` no se mezcla con el `token` anterior. Sin embargo, **`ready` no está garantizado que sea el primer mensaje**: tanto los `status` del health check como un `status` difundido a todas las sesiones por `ClientRegistry` mientras la sesión ya está registrada pero `ready` aún no se ha enviado, pueden preceder a `ready`. Los clientes deben estar preparados para recibir `status` antes de `ready`.

---

## 6. Recibir audio TTS

Requiere `"audio"` en `output_mode`.

Los frames de audio llegan como **mensajes binarios** intercalados con los JSON de control.

### Formato de los frames binarios

```
┌──────────┬──────────────────────┬─────────────────────┐
│  0xA1    │  turn_seq (uint16 BE) │  PCM16 24 kHz       │
│  1 byte  │  2 bytes              │  N bytes            │
└──────────┴──────────────────────┴─────────────────────┘
```

| Parámetro | Valor |
|-----------|-------|
| Magic byte | `0xA1` — identifica frames de audio |
| `turn_seq` | Uint16 big-endian — mismo valor que `turn_start.turn_seq`. Es el contador de turnos módulo 65 536: tras el turno 65 535 vuelve a 0 (el `turn_id` `t-N` no se reinicia), así que compara por igualdad y no asumas que crece siempre |
| Audio | PCM16 signed 16-bit little-endian, 24 000 Hz, 1 canal (mono) |

El `turn_seq` permite descartar audio de turnos anteriores: un frame cuyo `turn_seq` **no coincide** con el del último `turn_start` recibido no es del turno actual. Es módulo 65 536 y no es monótono tras el desbordamiento, así que **no compares "mayor/menor"**: compara por igualdad (o usa `turn_id` en los mensajes JSON).

### Ritmo y tamaño de los frames

El gateway envía el audio a ritmo de reproducción, con ~`AUDIO_LEAD_S` (2 s por defecto) de adelanto, en frames de ≤200 ms (9 600 bytes de PCM). No dependas del tamaño de los frames. `turn_end` llega ~2 s antes de que termine de sonar el audio: es el fin de la respuesta, no del sonido.

### Cómo parsear un frame

```python
def parse_audio_frame(data: bytes):
    if len(data) < 3 or data[0] != 0xA1:
        return None, None  # no es un frame de audio
    turn_seq = (data[1] << 8) | data[2]
    pcm16 = data[3:]
    return turn_seq, pcm16
```

### Pseudocódigo con barge-in por turn_seq

```python
current_turn_seq = 0
audio_buffer = bytearray()

async for msg in ws:
    if isinstance(msg, bytes):
        if len(msg) >= 3 and msg[0] == 0xA1:
            frame_seq = (msg[1] << 8) | msg[2]
            pcm16 = msg[3:]
            if frame_seq == current_turn_seq:
                # Audio del turno actual — reproducir. Los frames de otro turno
                # (anterior, ya cancelado) se descartan; nunca compares con > o <.
                audio_player.write(pcm16)

    elif isinstance(msg, str):
        data = json.loads(msg)
        if data["type"] == "turn_start":
            current_turn_seq = data["turn_seq"]
            audio_player.prepare_for_new_turn()
        elif data["type"] == "interrupted":
            audio_player.stop()
```

> Si el TTS no está disponible, el gateway continúa sin audio — los `token` siguen llegando igualmente. `live_capabilities.tts` en el `ready` te avisa de antemano.

---

## 7. Barge-in e interrupciones

El barge-in ocurre cuando el usuario empieza a hablar mientras el orquestador aún está respondiendo.

El gateway detecta la parcial de transcripción y cancela el turno activo si el barge-in está habilitado (`requested_capabilities.barge_in`; `live_capabilities.barge_in` indica si está operativo) y la parcial supera el umbral mínimo de caracteres. El cliente recibe:

```json
{ "type": "interrupted", "turn_id": "t-1" }
```

`turn_id` es el turno cancelado (puede faltar si el turno aún no había enviado su `turn_start`). El gateway cierra todo turno cancelado con su propio `turn_end` **antes** de `interrupted`: el orden es `turn_end` (t-1) → `interrupted` (t-1) → `turn_start` (t-2).

**Un push que está sonando también se corta** (§10): el barge-in, un `send` y un `cancel` lo cierran igual que a un turno normal. Recibes su `turn_end` (exactamente uno) antes de cualquier otra cosa del turno nuevo; en el barge-in, `interrupted` lleva el `turn_id` del push. Su audio deja de llegar. La transcripción final por sí sola no lo corta: lo corta el `send` que la sigue.

Tras `interrupted`:
- Limpia la UI de la respuesta en curso
- Descarta audio con `turn_seq` del turno cancelado (ya no llegará más)
- El flujo reinicia normalmente — sigue enviando audio

El nuevo turno llegará con un `turn_start` con un `turn_seq` distinto (módulo 65 536, no necesariamente mayor), lo que también activa el descarte de audio del turno anterior a nivel de frame (por igualdad de `turn_seq`) sin necesitar el mensaje `interrupted`.

---

## 8. Estado de servicios

El gateway notifica cambios en el estado de sus microservicios durante la sesión:

```json
{ "type": "status", "service": "tts", "state": "degraded" }
```

Los tres microservicios downstream (orquestador, Transcriber, TTS) tienen reconexión automática con backoff exponencial. El vocabulario de conectividad es:

| `state` | Significado |
|---------|-------------|
| `unavailable` | El servicio está caído. Puede o no estar reintentando activamente en segundo plano (ver tabla siguiente). |
| `reconnecting` | Reintentando activamente ahora mismo. |
| `restored` | Volvió a la normalidad tras haber estado `unavailable`/`reconnecting`. |
| `degraded` | Solo para `transcriber` — señal de **calidad**, no de conectividad (silencio prolongado **mientras envías audio** — con un turno en curso o sin mandar audio, p. ej. sesión persistente en reposo con solo `keepalive`, no se cuenta silencio; buffer lleno, GPU saturada con `code: "gpu_saturated"`, o proveedor externo sin capacidad momentánea con `code: "provider_capacity"`). No forma parte del ciclo unavailable→reconnecting→restored. Cuando la causa es saturación del transcriber (`gpu_saturated` o `provider_capacity`), el aviso de recuperación es `restored`; el resto de causas de `degraded` no tienen aviso de recuperación. |

Por servicio:

| `service` | Comportamiento e impacto en la sesión |
|-----------|-----------------------------------------|
| `orchestrator` | Reintenta en segundo plano con backoff mientras la sesión está activa. Si cae **antes o durante el handshake** (health check inicial), es el único caso fatal: el gateway cierra el WebSocket con 1011 y nunca llega a enviar `ready` (ver §1). Si cae **durante una sesión ya establecida**, el WebSocket **no se cierra** — solo falla el turno en curso (`error` con `code: "TURN_ERROR"`, `fatal: false`) y **todas** las sesiones conectadas reciben proactivamente `status: {orchestrator, reconnecting}` al caer y `status: {orchestrator, restored}` al volver, incluso si están inactivas en ese momento (no hace falta intentar un turno para enterarte). **Una sola fase de reintento, sin tope (#272):** el gateway reintenta sin parar (backoff exponencial acotado por `ORCHESTRATOR_RECONNECT_MAX_BACKOFF`, 60 s por defecto), así que tras una caída larga el estado sigue siendo `reconnecting` —nunca pasa a `unavailable` por llevar mucho tiempo caído— y se recibe un único `restored` al recuperarse. `unavailable` solo aparece si la sesión se abre con el orquestador aún sin conectar. `GET /ready` responde 503 mientras OpenClaw esté caído, sea cual sea el tiempo transcurrido. |
| `transcriber` | Una sesión STT por sesión de audio (con el proveedor `fogar`, una conexión a fogar-transcriber); reconecta en segundo plano. `unavailable` significa que no hay transcripción automática — puedes seguir enviando texto con `send`. Puede llegar desde el arranque mismo de la sesión (ver nota en §1) o en cualquier punto intermedio. Nunca cierra la sesión. |
| `tts` | Sin conexión persistente — TTS se reconstruye en cada turno por diseño. `reconnecting`/`unavailable` aquí significa "el último intento falló y el siguiente turno con audio puede fallar también hasta que pase el backoff" — no hay un reintento en segundo plano independiente del propio turno. Los `token` de texto siguen llegando igual; solo falta el audio. |

Los mensajes de estado también pueden incluir `code` y `message` con detalles adicionales:

```json
{
  "type": "status",
  "service": "transcriber",
  "state": "degraded",
  "code": "buffer_full",
  "message": "buffer_full"
}
```

---

## 9. Mensajes de error

Los errores llegan independientemente de `output_mode`:

```json
{
  "type": "error",
  "code": "TURN_ERROR",
  "message": "orchestrator_unavailable",
  "fatal": false,
  "turn_id": "t-2"
}
```

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `code` | string | Código de error (ver tabla abajo) |
| `message` | string | Descripción legible |
| `fatal` | bool | Si `true`, el gateway cierra la sesión inmediatamente después |
| `turn_id` | string | Presente solo si el error está ligado a un turno concreto |

### Códigos de error

> **Nota de precisión:** en la implementación actual, los únicos códigos que el gateway envía de verdad como mensaje `{"type":"error",...}` son `TURN_ERROR` (fallo dentro de un turno ya en marcha) e `INVALID_MESSAGE` (mensaje del cliente mal formado), ambos con `fatal: false`. Los fallos de handshake (`client_key` inválida, agente inexistente, orquestador no disponible al arrancar) **no** se comunican con un mensaje `error` previo — el gateway cierra directamente el WebSocket con el código y `reason` de la tabla de la §1. La tabla siguiente documenta la intención original del formato `code`/`fatal`; trátala como referencia de forma, no como lista exhaustiva de códigos que verás en producción hoy.

| Código | Fatal | Cuándo ocurre |
|--------|-------|---------------|
| `TURN_ERROR` | false | Fallo en un turno concreto (orquestador caído/reconectando durante una sesión activa, **o** el turno se cuelga sin progreso — `message: "turn_timeout"`, ver §13); la sesión continúa |
| `INVALID_MESSAGE` | false | Mensaje de texto del cliente que no es un objeto JSON (`42`, `[1]`, `"s"`, `true`, `null`) o `send` con `text` que no es cadena (#215). La sesión continúa; el mensaje se ignora. En modo texto, un prompt plano que parsea como JSON escalar (`"42"`, `"true"`, `"null"`) también recibe `INVALID_MESSAGE`: los prompts van en `{"type":"send","text":...}` |
| `AUTH_FAILED` | true | Documentado para `client_key` inválida o inactiva — en la práctica se señaliza cerrando el WS con 1008, sin este mensaje previo |
| `AGENT_NOT_FOUND` | true | Documentado para agente inexistente — en la práctica se señaliza cerrando el WS con 1008, sin este mensaje previo |
| `ORCHESTRATOR_UNAVAILABLE` | true | Documentado para orquestador no disponible al iniciar — en la práctica se señaliza cerrando el WS con 1011, sin este mensaje previo |
| `TTS_UNAVAILABLE` | false | Documentado para TTS caído — en la práctica ver `status: {tts, unavailable}` (§8), no este código |
| `TRANSCRIBER_UNAVAILABLE` | false | Documentado para Transcriber caído — en la práctica ver `status: {transcriber, unavailable}` (§8), no este código |
| `INTERNAL_ERROR` | true/false | Reservado para error inesperado; no confirmado en el código actual |

#### Valores de `message` de `TURN_ERROR` (estables — #228)

El `message` de un `TURN_ERROR` es siempre uno de estos códigos fijos, nunca el texto de una excepción ni un texto interno de OpenClaw (que se queda en el servidor, y ni siquiera en sus logs). Los mismos códigos llegan a los llamadores de `/v1/chat/completions` (`{"error": "<código>"}` con 502, o `error.message` dentro del flujo SSE). Cualquier otro fallo interno inesperado en `/v1` llega como `internal_error`.

| `message` | Cuándo |
|---|---|
| `orchestrator_unavailable` | El orquestador (OpenClaw) no está conectado o está reconectando al empezar el turno |
| `orchestrator_send_failed` | El gateway no pudo escribir la petición del turno al orquestador (conexión rota) |
| `orchestrator_error` | OpenClaw rechazó el turno (`res` con `ok:false`), el agente falló después de aceptarlo, o falló algo interno en el camino hacia él |
| `orchestrator_disconnected` | La conexión con el orquestador se cerró con el turno en vuelo |
| `turn_timeout` | El orquestador no mandó nada durante `TURN_TIMEOUT_S` (§13) |
| `turn_overflow` | El gateway no pudo procesar los eventos del turno al ritmo de llegada (§13) |
| `turn_in_progress` | Ya hay un turno en curso para esa sesión (`/v1`: HTTP 409) |

**Cambio de contrato (#228):** antes, `message` podía ser `send failed: <texto de la excepción>`, `not connected`, el `error` crudo de OpenClaw (p. ej. `{'code': ..., 'message': ...}`) o el `str(e)` de una excepción interna. Si tu cliente comparaba esos textos, usa los códigos de la tabla.

Si `fatal: true`, no envíes más mensajes — el servidor cerrará la conexión WS inmediatamente.

---

## 10. Turns iniciados por el agente (push)

OpenClaw puede iniciar turnos proactivamente sin que el usuario haya enviado nada (notificaciones, recordatorios, alertas). El gateway los entrega al cliente con el mismo protocolo que un turno normal:

```json
{ "type": "turn_start", "turn_id": "t-3", "turn_seq": 3 }
{ "type": "token", "turn_id": "t-3", "text": "Recuerda que tienes una reunión en 5 minutos." }
// Si audio en output_mode: [0xA1][0x00][0x03][PCM16...]
{ "type": "turn_end", "turn_id": "t-3" }
```

Un push que llega mientras suena una respuesta tuya se entrega después del `turn_end` de esa respuesta, nunca encima (los pushes siguientes esperan en orden).

Al revés, hablar corta el push: si el usuario hace barge-in, envía un `send` o un `cancel` mientras un push está abierto, el gateway para su audio, te envía su `turn_end` y, si el push es de tu misma sesión de agente, lo aborta en OpenClaw. Solo después arranca el turno del usuario, así que nunca hay dos turnos abiertos a la vez (#202). Lo que quedara de ese push no llega.

El cliente no necesita distinguirlos de los turnos normales — el `turn_id` y `turn_seq` siguen la misma secuencia. Si `tool_calls_enabled` está activo, también pueden llegar mensajes `tool_call` (§5) intercalados.

> **`push_enabled` (por defecto `True`, configurable por admin):** si tu `client_key` tiene este flag desactivado, no recibirás **ningún** mensaje de un turno iniciado por el agente — ni `turn_start`, ni `token`, ni audio, ni `tool_call` — para esa sesión. No hay ninguna señal explícita de que se haya suprimido un push; es indistinguible de que OpenClaw simplemente no haya iniciado ninguno. Si tu integración depende de notificaciones proactivas, confirma con el admin que `push_enabled=True` para tu cliente.

> **Garantía de un único par por respuesta (issue #84):** cuando el agente hace tool use o razonamiento multi-paso, OpenClaw puede emitir varios eventos internos de inicio/fin para una sola respuesta LLM. El gateway los colapsa siempre en exactamente **un** `turn_start`/`turn_end` de cara al cliente — nunca verás duplicados. Si tu cliente implementó algún workaround para deduplicar `turn_end` repetidos (grace period, etc.) porque llegaban 2-3 veces por turno, ya no hace falta; puedes simplificarlo o quitarlo con seguridad.

> **Coordinación con turnos normales (issue #112):** si ya hay un turno normal en curso (uno que
> tú mismo iniciaste con `send`), ningún evento `agent` interno de OpenClaw para esa misma sesión
> abrirá un `turn_start` de push superpuesto al tuyo — el contenido de ese turno (tokens,
> `tool_call`) te sigue llegando con normalidad dentro del turno que ya tenías abierto. Si un
> turno de push ya estaba en curso *antes* de que empezaras el tuyo, tu `send` lo corta: su
> `turn_end` llega antes del `turn_start` de tu turno.

---

## 11. Modos de operación

### Chat de texto puro

```json
// Handshake
{ "client_key": "...", "input_mode": "text", "output_mode": ["text"] }

// Enviar pregunta
{ "type": "send", "text": "¿Qué tiempo hace en Madrid?" }

// Recibir respuesta
{ "type": "turn_start", "turn_id": "t-1", "turn_seq": 1 }
{ "type": "token", "turn_id": "t-1", "text": "En Madrid " }
{ "type": "token", "turn_id": "t-1", "text": "hace sol." }
{ "type": "turn_end", "turn_id": "t-1" }
```

### Asistente de voz completo (cliente de voz de Mac y Android, app móvil; ESP32 planificado)

```json
// Handshake
{ "client_key": "...", "input_mode": "audio", "output_mode": ["audio", "text", "status"] }
```

Flujo: envía PCM Float32 → recibe parciales → envía `end` → recibe `transcription` → envías `send` → recibes `turn_start` + `token` + frames binarios + `turn_end`.

### Voz de entrada, texto de salida

```json
// Handshake
{ "client_key": "...", "input_mode": "audio", "output_mode": ["text"] }
```

El transcriptor funciona normalmente pero no se activa TTS. Útil para clientes que quieren transcripción + tokens de texto sin reproducción de audio.

### Texto de entrada, audio de salida

```json
// Handshake
{ "client_key": "...", "input_mode": "text", "output_mode": ["text", "audio"] }
```

El cliente escribe; el gateway sintetiza audio con el texto de la respuesta.

---

## 12. Referencia completa de mensajes

### Cliente → Gateway

| Mensaje | Formato | Cuándo |
|---------|---------|--------|
| Handshake | `{"client_key":"...","input_mode":"...","output_mode":[...],"agent":"..."}` | Primer mensaje, obligatorio |
| Fin de audio | `{"type":"end"}` | `input_mode="audio"` — usuario termina de hablar |
| Enviar texto | `{"type":"send","text":"..."}` | Envía texto al orquestador |
| Cancelar turno | `{"type":"cancel"}` | Cancela turno activo (o el push que suena, §10) sin iniciar uno nuevo |
| Frame de audio | bytes PCM Float32 16 kHz sin header | `input_mode="audio"` |
| Otro `type` (p. ej. `{"type":"keepalive"}`) | `{"type":"..."}` | Se ignora, pero cuenta como actividad para `IDLE_TIMEOUT_S` (§13) |

### Gateway → Cliente

#### Mensajes JSON

| Tipo | Formato | Cuándo |
|------|---------|--------|
| `ready` | `{"type":"ready","session_id":"...","agent":"...","input_mode":"...","output_mode":[...],"requested_capabilities":{...},"live_capabilities":{...}}` | Tras handshake exitoso; puede ir precedido de `status` |
| `turn_start` | `{"type":"turn_start","turn_id":"t-N","turn_seq":N mod 65536}` | Inicio de cada turno |
| `token` | `{"type":"token","turn_id":"t-N","text":"..."}` | `"text"` en `output_mode` — tokens en streaming |
| `turn_end` | `{"type":"turn_end","turn_id":"t-N"}` | Fin de cada turno |
| `tool_call` | `{"type":"tool_call","turn_id":"t-N","phase":"start"\|"result","name":"...","tool_call_id":"...","args":{...}\|null,"result":"..."\|null,"is_error":bool\|null}` | Solo si `tool_calls_enabled=True` para el cliente — uso de herramientas del agente |
| `transcription_partial` | `{"type":"transcription_partial","text":"..."}` | `input_mode="audio"` — parciales en tiempo real |
| `transcription` | `{"type":"transcription","text":"..."}` | `input_mode="audio"` — transcripción final, espera `send` |
| `interrupted` | `{"type":"interrupted","turn_id":"t-N"}` | Barge-in confirmado, turno anterior cancelado (`turn_id` puede faltar si el turno aún no había enviado su `turn_start`; ver §7) |
| `status` | `{"type":"status","service":"...","state":"..."}` | Cambio de estado de un microservicio |
| `error` | `{"type":"error","code":"...","message":"...","fatal":bool,"turn_id":"..."}` | Error (turno o sesión) |
| `pipeline_event` | `{"type":"pipeline_event","stage":"...","elapsed_ms":N,"turn":N}` | Solo con `"status"` en `output_mode`: cada etapa del pipeline (p. ej. `llm_start`, `llm_first_token`, `tts_first_chunk`, `transcription_final`, `session_end`) con los ms desde la etapa anterior. Diagnóstico; no lleva texto |

#### Frames binarios de audio

```
[0xA1][turn_seq uint16 BE][PCM16 24kHz LE mono]
```

Llegan entrelazados con los mensajes JSON. Identifica audio por el magic byte `0xA1` en el primer byte.

---

## 13. Timeouts y cierre de sesión

**Nuevo — issue #115 (v1.17.0).** El gateway acota cuatro esperas que antes eran indefinidas. Tres son visibles en el wire; la cuarta (hoy `SESSION_TEARDOWN_S`, #221) es puramente interna. **Nuevo — issue #130 (S3a).** Se añade una quinta, `PUSH_TTS_DRAIN_TIMEOUT_S`, que en el wire solo se nota como audio de un turno de push que se corta. **Nuevo — issue #130 (S2).** Se añade una sexta, `CLIENT_SEND_TIMEOUT_S` (última fila): si tu socket drena por debajo del ritmo al que el gateway escribe (un cliente parado, o uno que lee demasiado despacio), el gateway te cierra con el código **1013** (`client too slow`). Un cliente que lee a un ritmo razonable no se ve afectado: en la práctica solo ocurre si tu socket se queda parado o si hay más datos en vuelo que los buffers del sistema (varios MB, más de ~2 minutos de audio TTS enviado de golpe).

| Deadline | Valor por defecto | Qué provoca | Cómo lo ves |
|---|---|---|---|
| `HANDSHAKE_TIMEOUT_S` | 5s | No mandas el JSON de handshake a tiempo | Cierre WS 1008, `"Handshake timeout"` (ver §1) |
| `TURN_TIMEOUT_S` | 120s, **idle-reset** | El orquestador deja de mandar nada durante un turno ya en marcha (cuelgue real, no duración total) | `{"type":"error","code":"TURN_ERROR","message":"turn_timeout","fatal":false,"turn_id":"..."}` (ver §9) — la sesión sigue viva, solo ese turno se aborta |
| `TURN_QUEUE_MAX` | 1000 eventos por turno | El gateway no consigue procesar los eventos del orquestador al ritmo al que llegan para ese turno (consumidor atascado); es un fallo interno raro, no depende de tu lectura del socket | `{"type":"error","code":"TURN_ERROR","message":"turn_overflow","fatal":false,"turn_id":"..."}` — la sesión sigue viva, solo ese turno se aborta (los tokens pendientes se descartan); mismo tratamiento en el cliente que `turn_timeout` |
| `IDLE_TIMEOUT_S` | 300s (5 min) | No mandas **ningún** mensaje (ni audio ni texto) durante ese tiempo | Cierre WS con código 1000, **sin ningún mensaje de error o aviso previo** |
| `silence_timeout_s` / `max_silence_turns` (por cliente, defaults 2 s y 3) | ~6 s con los defaults | Mientras envías audio, el transcriptor no devuelve nada: cada sondeo de 2 s pasado `silence_timeout_s` cuenta un aviso (`status` `transcriber` `degraded`); con `max_silence_turns` avisos seguidos se cierra la sesión. No cuenta con un turno en curso ni sin audio entrante | Avisos `degraded` y después cierre WS con código 1000 |
| `SESSION_REVALIDATE_INTERVAL_S` | 10s | Tu cliente se borra, se desactiva o se le rota la `client_key` con la sesión abierta | Cierre WS 1008 `"revoked"` en la siguiente revalidación; el turno en curso se cancela |
| `SESSION_TEARDOWN_S` | 5s | Interno — tope de cada paso cuando el gateway cierra una sesión (cancelar el turno en curso, cerrar servicios, mandarte los últimos mensajes). Sustituye a `SHUTDOWN_DRAIN_S`, que ya no se usa | Ninguno directo. Un turno en curso **no se espera**: se cancela, y recibes su `turn_end` si el socket sigue abierto (ver «Cierre de la sesión por el gateway» abajo) |
| `PUSH_TTS_DRAIN_TIMEOUT_S` | 30s, **sin progreso** | El TTS de un turno de push (iniciado por el agente) lleva 30s sin producir audio tras el `end` del agente (se reinicia con cada trozo, así que un push largo sonando a ritmo no se corta) | El audio restante de ese push se corta, el TTS se cierra y el `turn_end` se envía igualmente — la sesión sigue viva. Con el mismo tope se acotan las escrituras de texto al TTS de push y su `end()`: si vencen, el resto de ese push llega solo como `token` de texto, sin más audio, y el `turn_end` se envía igualmente. En los turnos normales el mismo tope acota cada operación con el TTS, y la espera del audio cuenta como tiempo **sin progreso** (se reinicia con cada chunk): si vence, el turno sigue solo con texto y también recibe su `turn_end` |
| `CLIENT_SEND_TIMEOUT_S` | 10s, **por envío** | El gateway no consigue escribirte un mensaje durante ese tiempo porque tu socket **drena por debajo del ritmo al que el gateway escribe**: o has dejado de leer, o lees muy por debajo del caudal de salida (el audio TTS PCM16 a 24 kHz son 48 KB/s). Es un umbral de caudal de drenado, del orden de unos pocos KB/s (teoría) a unas pocas decenas de KB/s (medidas de laboratorio en macOS), según kernel y tasa de producción, y **solo cuando hay más datos en vuelo que los buffers del sistema** (varios MB); con respuestas de tamaño normal y un cliente que lee, no se activa; un cliente que lee a un ritmo razonable no se corta | Cierre WS con código **1013** (`client too slow`) (mejor esfuerzo) si reanudas la lectura en los 2 s siguientes; si no, o si el close frame se pierde, la conexión se corta sin close frame (1006). Los mensajes pendientes se descartan; reconecta y abre una sesión nueva |

### Cuidado con el ping de la librería WebSocket de tu cliente

Muchas librerías WebSocket (p. ej. `websockets` de Python: `ping_interval=20 s` y `ping_timeout=20 s` por defecto) cierran la conexión si no reciben el `pong` a tiempo. Con el pacing del audio (§6) el gateway solo tiene ~2 s de audio por delante, así que el `pong` ya no queda detrás de minutos de audio. Aun así, si tu cliente deja de leer el socket mientras reproduce, la librería puede quedarse sin `pong` y **cerrar ella misma la conexión** (1011/1006) sin que el gateway haga nada.

Qué hacer en el cliente: desactivar o ampliar el ping de la librería (`ping_interval=None` o un `ping_timeout` mayor), o leer del socket en una tarea aparte de la reproducción y poner el audio en una cola propia. Y, en cualquier caso, **reconectar ante cualquier cierre inesperado** (1006, 1011, 1012, 1013, EOF) abriendo una sesión nueva con handshake completo.

### `TURN_TIMEOUT_S` es "idle-reset", no un techo total

El reloj se reinicia con cada evento que llega del orquestador (cada token, cada evento de herramienta). Un turno con una respuesta larga pero activa (tool-use multi-paso, por ejemplo) nunca se corta por esto — solo se corta si el orquestador deja de mandar absolutamente nada durante 120s seguidos. No necesitas ningún cambio de cliente para esto: ya manejas `TURN_ERROR` (§9), y `turn_timeout` es simplemente un valor más de `message` dentro de ese mismo mecanismo.

### `IDLE_TIMEOUT_S` — la que sí te afecta si mantienes conexiones abiertas

Esta es la novedad más relevante para clientes de larga duración (dispositivos siempre-conectados, sesiones que solo reciben *pushes* del agente sin que el usuario hable):

- El contador se basa **solo en mensajes entrantes del cliente** (audio o texto) — nada que el gateway te mande a ti cuenta como actividad.
- **Se pausa mientras haya un turno o un push en curso**: si el orquestador está respondiendo activamente o hay un turno iniciado por el agente abierto, el cierre por idle no se dispara aunque hayan pasado los 5 minutos — el contador solo corre cuando de verdad no hay nada pasando en ninguna dirección.
- Si se dispara, el gateway simplemente cierra el WebSocket (código 1000, cierre normal) — **no** manda un `error` ni un `status` avisando antes. Detectas esto como cualquier otro cierre inesperado de socket.
- No hay campo en `ready` que indique este valor — si tu cliente necesita conocerlo para decidir cuándo mandar un keep-alive, tiene que estar hardcodeado o configurado del lado del cliente (no se expone por protocolo todavía).

**Qué implica para tu cliente:** si tu caso de uso implica abrir una sesión y quedarte a la escucha de *pushes* del agente sin que el usuario interactúe activamente durante más de 5 minutos seguidos, tu cliente debe:
1. Mandar algo periódicamente para resetear el contador (aunque sea un mensaje que el gateway ignore a nivel de negocio, cualquier frame válido cuenta), **o**
2. Implementar reconexión automática tras un cierre inesperado del WebSocket con código 1000 y sin haber recibido `error` — no lo trates como un fallo, es un cierre esperado por inactividad.

### Cierre de la sesión por el gateway — informativo

No requiere ningún cambio de cliente si ya reconectas tras un cierre inesperado.

- **Turno en curso:** si el gateway cierra la sesión con un turno abierto, el turno se **cancela**, no se espera a que termine, y recibes igualmente su `turn_end` antes del cierre si el socket sigue abierto. Nunca queda un `turn_start` sin su `turn_end` mientras puedas recibirlo. La inactividad (`IDLE_TIMEOUT_S`) y el silencio no cortan un turno en curso; lo único abierto que pueden cerrar es un push huérfano (abierto más de `TURN_TIMEOUT_S` sin su fin), que también recibe su `turn_end`.
- **Reinicio o parada del gateway:** el servidor cierra cada WebSocket con el código **1012** (*service restart*) en cuanto empieza a apagarse; un turno en curso se corta en ese momento y su `turn_end` ya no te llega (el socket está cerrado). No hay ventana de gracia para terminar el turno. Trata el 1012 como un cierre esperado: reconecta (con una pequeña espera y reintentos, el gateway tarda unos segundos en volver) y abre una sesión nueva con handshake completo.
- **Revocación:** se cierra con 1008 `"revoked"` antes de cancelar el turno, así que tampoco recibes su `turn_end`.

`SHUTDOWN_DRAIN_S` (antes 30 s) prometía esperar al turno en curso en un reinicio; en la práctica eso no ocurría (el servidor ya había cerrado el socket). Desde #221 ya no se usa: el gateway lo ignora y avisa en el log si sigue en su `.env`.
