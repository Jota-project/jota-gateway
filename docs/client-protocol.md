# Protocolo de cliente — jota-gateway

Guía completa para implementar clientes que se conecten a jota-gateway. Cubre el ciclo de vida de la sesión WebSocket, todos los mensajes posibles en ambas direcciones y el formato de audio binario.

---

## Índice

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
| `output_mode` | array | ✓ | Qué quiere recibir: `"text"`, `"audio"`, `"status"` |
| `agent` | string | — | Agente OpenClaw a usar; omitir para usar el agente por defecto (ver "Selección de agente" abajo) |

### Selección de agente

El agente efectivo de la sesión no es simplemente "el que pediste o el global": el gateway resuelve una cascada server-side (`requested` → `default_agent` configurado por el admin para tu `client_key` → default global de OpenClaw → `"main"`) y aplica dos comprobaciones de política antes de aceptar la conexión — ver `CLAUDE.md` ("Session key derivation") para el detalle completo. Como cliente solo necesitas saber dos cosas:

1. Si omites `agent` en el handshake, el que acabes usando puede venir de una configuración por-cliente que un admin te haya asignado (no siempre el default global) — comprueba siempre `ready.agent`, no asumas cuál será.
2. Pedir explícitamente un `agent` puede fallar de dos formas distintas (ver tabla siguiente): que ese agente no esté permitido para tu cliente, o que no exista en OpenClaw.

### Errores de handshake (el servidor cierra la conexión)

| Situación | Código WS | Motivo |
|-----------|-----------|--------|
| No se recibe el handshake JSON en los primeros `HANDSHAKE_TIMEOUT_S` (10s por defecto) | 1008 | `"Handshake timeout"` |
| JSON inválido o campos incorrectos | 1008 | `"Handshake invalido"` |
| `client_key` inválida o cliente inactivo | 1008 | `"Clave de cliente invalida o inactiva"` |
| Servicio de identidad no disponible | 1011 | `"Servicio de identidad no disponible"` |
| Agente solicitado no está en la lista permitida para este cliente (`allowed_agents`) | 1008 | `"Agent '{name}' not permitted for this client."` |
| Agente solicitado no existe en OpenClaw | 1008 | `"Agent '{name}' not available."` |
| Orquestador no disponible tras health check | 1011 | `"Servicio crítico no disponible"` |

> **El orquestador (OpenClaw) es el único servicio que puede cerrar la conexión en el handshake.** Transcriber y TTS ya **no** son críticos: si cualquiera de los dos no responde al arrancar la sesión, el gateway la abre igualmente en modo degradado y te lo notifica vía `status` (ver §8) — nunca cierra el WebSocket por esto. Antes de la versión que introdujo la reconexión automática (issue #46), un fallo de Transcriber en este punto sí cerraba la conexión con 1011; ya no es el caso.
>
> Estos mensajes `status` de arranque pueden llegar **antes** del `ready` (el health check se ejecuta justo antes de enviarlo) — no asumas que `ready` es el primer mensaje posible tras el handshake, solo que es el primero que confirma que la sesión quedó establecida.

---

## 2. Mensaje ready

Si el handshake es válido y todos los servicios críticos responden, el gateway envía `ready` como **primer mensaje** antes de cualquier otro:

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
| `live_capabilities.barge_in` | Si el barge-in está operativos (requiere transcriber activo) |
| `live_capabilities.tts` | Si TTS responde en este momento |
| `live_capabilities.transcriber` | Si el transcriptor está conectado (`input_mode == "audio"`) |

Espera este mensaje antes de enviar audio o texto. Si no llega, la conexión fue cerrada por un error de handshake.

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
| `turn_seq` | El mismo N como entero — aparece también en el header de los frames de audio |

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

Señala que el orquestador terminó de generar la respuesta. El audio TTS puede seguir llegando brevemente después (ya está en tránsito).

### `tool_call` — uso de herramientas del agente (opt-in)

Solo se envía si el cliente tiene el flag `tool_calls_enabled` activado (por defecto `False`, gestionable vía Admin API — ver `CLAUDE.md`). Cuando el agente invoca una herramienta durante el turno, llegan dos mensajes intercalados con los `token`:

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
| `turn_seq` | Uint16 big-endian — mismo valor que `turn_start.turn_seq` |
| Audio | PCM16 signed 16-bit little-endian, 24 000 Hz, 1 canal (mono) |

El `turn_seq` permite descartar audio de turnos anteriores si ya llegó un `turn_start` con seq mayor (barge-in sin mensaje adicional).

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
            if frame_seq >= current_turn_seq:
                # Audio del turno actual o más nuevo — reproducir
                if frame_seq > current_turn_seq:
                    audio_player.stop()  # barge-in, descarta audio anterior
                    current_turn_seq = frame_seq
                audio_player.write(pcm16)

    elif isinstance(msg, str):
        data = json.loads(msg)
        if data["type"] == "turn_start":
            current_turn_seq = data["turn_seq"]
            audio_player.prepare_for_new_turn()
        elif data["type"] == "interrupted":
            audio_player.stop()
```

> Si el TTS no está disponible, el gateway continúa sin audio — los `token` siguen llegando igualmente. `capabilities.tts` en el `ready` te avisa de antemano.

---

## 7. Barge-in e interrupciones

El barge-in ocurre cuando el usuario empieza a hablar mientras el orquestador aún está respondiendo.

El gateway detecta la parcial de transcripción y cancela el turno activo si supera el umbral mínimo de caracteres (`capabilities.barge_in`). El cliente recibe:

```json
{ "type": "interrupted" }
```

Tras `interrupted`:
- Limpia la UI de la respuesta en curso
- Descarta audio con `turn_seq` del turno cancelado (ya no llegará más)
- El flujo reinicia normalmente — sigue enviando audio

El nuevo turno llegará con un `turn_start` con `turn_seq` mayor, lo que también activa el descarte de audio del turn anterior a nivel de frame sin necesitar el mensaje `interrupted`.

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
| `degraded` | Solo para `transcriber` — señal de **calidad**, no de conectividad (silencio prolongado, buffer lleno). No forma parte del ciclo unavailable→reconnecting→restored. |

Por servicio:

| `service` | Comportamiento e impacto en la sesión |
|-----------|-----------------------------------------|
| `orchestrator` | Reintenta en segundo plano con backoff mientras la sesión está activa. Si cae **antes o durante el handshake** (health check inicial), es el único caso fatal: el gateway cierra el WebSocket con 1011 y nunca llega a enviar `ready` (ver §1). Si cae **durante una sesión ya establecida**, el WebSocket **no se cierra** — solo falla el turno en curso (`error` con `code: "TURN_ERROR"`, `fatal: false`) y **todas** las sesiones conectadas reciben proactivamente `status: {orchestrator, unavailable→reconnecting→restored}`, incluso si están inactivas en ese momento (no hace falta intentar un turno para enterarte). |
| `transcriber` | Un `TranscriberClient` por sesión de audio; reconecta en segundo plano. `unavailable` significa que no hay transcripción automática — puedes seguir enviando texto con `send`. Puede llegar desde el arranque mismo de la sesión (ver nota en §1) o en cualquier punto intermedio. Nunca cierra la sesión. |
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
  "message": "Error al conectar con el orquestador",
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

> **Nota de precisión:** en la implementación actual, el único código que el gateway envía de verdad como mensaje `{"type":"error",...}` es `TURN_ERROR` (fallo dentro de un turno ya en marcha, `fatal: false`). Los fallos de handshake (`client_key` inválida, agente inexistente, orquestador no disponible al arrancar) **no** se comunican con un mensaje `error` previo — el gateway cierra directamente el WebSocket con el código y `reason` de la tabla de la §1. La tabla siguiente documenta la intención original del formato `code`/`fatal`; trátala como referencia de forma, no como lista exhaustiva de códigos que verás en producción hoy.

| Código | Fatal | Cuándo ocurre |
|--------|-------|---------------|
| `TURN_ERROR` | false | Fallo en un turno concreto (orquestador caído/reconectando durante una sesión activa, **o** el turno se cuelga sin progreso — `message: "turn_timeout"`, ver §13); la sesión continúa — **el único código realmente emitido hoy** |
| `AUTH_FAILED` | true | Documentado para `client_key` inválida o inactiva — en la práctica se señaliza cerrando el WS con 1008, sin este mensaje previo |
| `AGENT_NOT_FOUND` | true | Documentado para agente inexistente — en la práctica se señaliza cerrando el WS con 1008, sin este mensaje previo |
| `ORCHESTRATOR_UNAVAILABLE` | true | Documentado para orquestador no disponible al iniciar — en la práctica se señaliza cerrando el WS con 1011, sin este mensaje previo |
| `TTS_UNAVAILABLE` | false | Documentado para TTS caído — en la práctica ver `status: {tts, unavailable}` (§8), no este código |
| `TRANSCRIBER_UNAVAILABLE` | false | Documentado para Transcriber caído — en la práctica ver `status: {transcriber, unavailable}` (§8), no este código |
| `INTERNAL_ERROR` | true/false | Reservado para error inesperado; no confirmado en el código actual |

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

El cliente no necesita distinguirlos de los turnos normales — el `turn_id` y `turn_seq` siguen la misma secuencia. Si `tool_calls_enabled` está activo, también pueden llegar mensajes `tool_call` (§5) intercalados.

> **`push_enabled` (por defecto `True`, configurable por admin):** si tu `client_key` tiene este flag desactivado, no recibirás **ningún** mensaje de un turno iniciado por el agente — ni `turn_start`, ni `token`, ni audio, ni `tool_call` — para esa sesión. No hay ninguna señal explícita de que se haya suprimido un push; es indistinguible de que OpenClaw simplemente no haya iniciado ninguno. Si tu integración depende de notificaciones proactivas, confirma con el admin que `push_enabled=True` para tu cliente.

> **Garantía de un único par por respuesta (issue #84):** cuando el agente hace tool use o razonamiento multi-paso, OpenClaw puede emitir varios eventos internos de inicio/fin para una sola respuesta LLM. El gateway los colapsa siempre en exactamente **un** `turn_start`/`turn_end` de cara al cliente — nunca verás duplicados. Si tu cliente implementó algún workaround para deduplicar `turn_end` repetidos (grace period, etc.) porque llegaban 2-3 veces por turno, ya no hace falta; puedes simplificarlo o quitarlo con seguridad.

> **Coordinación con turnos normales (issue #112):** si ya hay un turno normal en curso (uno que
> tú mismo iniciaste con `send`), ningún evento `agent` interno de OpenClaw para esa misma sesión
> abrirá un `turn_start` de push superpuesto al tuyo — el contenido de ese turno (tokens,
> `tool_call`) te sigue llegando con normalidad dentro del turno que ya tenías abierto. Si un
> turno de push ya estaba en curso *antes* de que empezaras el tuyo, su propio `turn_end` sigue
> llegando con normalidad para cerrarlo correctamente.

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

### Asistente de voz completo (ESP32, app móvil)

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
| Cancelar turno | `{"type":"cancel"}` | Cancela turno activo sin iniciar uno nuevo |
| Frame de audio | bytes PCM Float32 16 kHz sin header | `input_mode="audio"` |

### Gateway → Cliente

#### Mensajes JSON

| Tipo | Formato | Cuándo |
|------|---------|--------|
| `ready` | `{"type":"ready","session_id":"...","agent":"...","input_mode":"...","output_mode":[...],"requested_capabilities":{...},"live_capabilities":{...}}` | Tras handshake exitoso, antes de cualquier otro mensaje |
| `turn_start` | `{"type":"turn_start","turn_id":"t-N","turn_seq":N}` | Inicio de cada turno |
| `token` | `{"type":"token","turn_id":"t-N","text":"..."}` | `"text"` en `output_mode` — tokens en streaming |
| `turn_end` | `{"type":"turn_end","turn_id":"t-N"}` | Fin de cada turno |
| `tool_call` | `{"type":"tool_call","turn_id":"t-N","phase":"start"\|"result","name":"...","tool_call_id":"...","args":{...}\|null,"result":"..."\|null,"is_error":bool\|null}` | Solo si `tool_calls_enabled=True` para el cliente — uso de herramientas del agente |
| `transcription_partial` | `{"type":"transcription_partial","text":"..."}` | `input_mode="audio"` — parciales en tiempo real |
| `transcription` | `{"type":"transcription","text":"..."}` | `input_mode="audio"` — transcripción final, espera `send` |
| `interrupted` | `{"type":"interrupted"}` | Barge-in confirmado, turno anterior cancelado |
| `status` | `{"type":"status","service":"...","state":"..."}` | Cambio de estado de un microservicio |
| `error` | `{"type":"error","code":"...","message":"...","fatal":bool,"turn_id":"..."}` | Error (turno o sesión) |

#### Frames binarios de audio

```
[0xA1][turn_seq uint16 BE][PCM16 24kHz LE mono]
```

Llegan entrelazados con los mensajes JSON. Identifica audio por el magic byte `0xA1` en el primer byte.

---

## 13. Timeouts y cierre de sesión

**Nuevo — issue #115 (v1.17.0).** El gateway acota cuatro esperas que antes eran indefinidas. Tres son visibles en el wire; la cuarta es puramente interna.

| Deadline | Valor por defecto | Qué provoca | Cómo lo ves |
|---|---|---|---|
| `HANDSHAKE_TIMEOUT_S` | 10s | No mandas el JSON de handshake a tiempo | Cierre WS 1008, `"Handshake timeout"` (ver §1) |
| `TURN_TIMEOUT_S` | 120s, **idle-reset** | El orquestador deja de mandar nada durante un turno ya en marcha (cuelgue real, no duración total) | `{"type":"error","code":"TURN_ERROR","message":"turn_timeout","fatal":false,"turn_id":"..."}` (ver §9) — la sesión sigue viva, solo ese turno se aborta |
| `IDLE_TIMEOUT_S` | 300s (5 min) | No mandas **ningún** mensaje (ni audio ni texto) durante ese tiempo | Cierre WS con código 1000, **sin ningún mensaje de error o aviso previo** |
| `SHUTDOWN_DRAIN_S` | 30s | Interno — límite que el gateway se da a sí mismo para esperar un turno en curso al cerrar una sesión (reinicio del servidor, u otro camino de cierre) | Ninguno directo; en el peor caso el turno se corta sin `turn_end` |

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

### `SHUTDOWN_DRAIN_S` — informativo

No requiere ningún cambio de cliente. Si el gateway se reinicia con un turno tuyo en curso, tiene hasta 30s para dejarlo terminar antes de forzar el cierre. Si tu cliente ya maneja reconexión tras un cierre de socket inesperado durante un turno (recomendado en general, no solo por esto), ya estás cubierto.
