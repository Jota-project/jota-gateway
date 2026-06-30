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
| `agent` | string | — | Agente OpenClaw a usar; omitir para usar el agente por defecto |

### Errores de handshake (el servidor cierra la conexión)

| Situación | Código WS | Motivo |
|-----------|-----------|--------|
| JSON inválido o campos incorrectos | 1008 | `"Handshake invalido"` |
| `client_key` inválida o cliente inactivo | 1008 | `"Clave de cliente invalida o inactiva"` |
| Servicio de identidad no disponible | 1011 | `"Servicio de identidad no disponible"` |
| Agente solicitado no existe en OpenClaw | 1008 | `"Agent '{name}' not available"` |
| Servicio crítico no disponible tras health check | 1011 | `"Servicio crítico no disponible"` |

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
  "capabilities": {
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
| `capabilities.barge_in` | Si el barge-in está habilitado para este cliente |
| `capabilities.tts` | Si el audio TTS está disponible (`"audio"` en `output_mode` y TTS responde) |
| `capabilities.transcriber` | Si el transcriptor está activo (`input_mode == "audio"`) |

Espera este mensaje antes de enviar audio o texto. Si no llega, la conexión fue cerrada por un error de handshake.

---

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
                capabilities = data["capabilities"]

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

| `service` | `state` | Impacto |
|-----------|---------|---------|
| `orchestrator` | `unavailable` | **Fatal** — el gateway cerrará la sesión |
| `transcriber` | `unavailable` | Degradado — sin transcripción automática; puedes seguir en modo texto |
| `transcriber` | `degraded` | Parcial — el transcriptor responde pero detectó un problema (buffer lleno, etc.) |
| `tts` | `unavailable` | Degradado — sin audio; los tokens de texto siguen llegando |

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

| Código | Fatal | Cuándo ocurre |
|--------|-------|---------------|
| `AUTH_FAILED` | true | `client_key` inválida o inactiva |
| `AGENT_NOT_FOUND` | true | El agente solicitado no existe en OpenClaw |
| `ORCHESTRATOR_UNAVAILABLE` | true | OpenClaw no disponible al iniciar la sesión |
| `TTS_UNAVAILABLE` | false | TTS caído; la sesión continúa sin audio |
| `TRANSCRIBER_UNAVAILABLE` | false | Transcriber caído; puedes cambiar a modo texto |
| `TURN_ERROR` | false | Fallo en un turno concreto; la sesión continúa |
| `INTERNAL_ERROR` | true/false | Error inesperado; `fatal` según criticidad |

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

El cliente no necesita distinguirlos de los turnos normales — el `turn_id` y `turn_seq` siguen la misma secuencia.

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
| `ready` | `{"type":"ready","session_id":"...","agent":"...","input_mode":"...","output_mode":[...],"capabilities":{...}}` | Tras handshake exitoso, antes de cualquier otro mensaje |
| `turn_start` | `{"type":"turn_start","turn_id":"t-N","turn_seq":N}` | Inicio de cada turno |
| `token` | `{"type":"token","turn_id":"t-N","text":"..."}` | `"text"` en `output_mode` — tokens en streaming |
| `turn_end` | `{"type":"turn_end","turn_id":"t-N"}` | Fin de cada turno |
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
