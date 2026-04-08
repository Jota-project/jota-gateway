# Guía de protocolo para clientes — jota-gateway

Esta guía describe cómo conectarse al gateway, qué mensajes enviar y recibir, y cómo manejar audio bidireccional.

---

## Índice

1. [Conexión y handshake](#1-conexión-y-handshake)
2. [Enviar texto](#2-enviar-texto)
3. [Enviar audio de micrófono](#3-enviar-audio-de-micrófono)
4. [Recibir texto de la IA](#4-recibir-texto-de-la-ia)
5. [Recibir audio TTS](#5-recibir-audio-tts)
6. [Mensajes de estado y error](#6-mensajes-de-estado-y-error)
7. [Modos de operación combinados](#7-modos-de-operación-combinados)
8. [Referencia de mensajes](#8-referencia-de-mensajes)

---

## 1. Conexión y handshake

**Endpoint:** `ws://<host>:8004/ws/stream/<client_id>`

`client_id` es un identificador libre que eliges tú (p.ej. `"esp32-salon"`, `"web-user-42"`). Se usa para logs y como `user_id` en el orquestador.

**El primer mensaje que envíes DEBE ser el handshake** — un JSON que declara qué modos usará este cliente:

```json
{
  "input_mode": "text",
  "output_mode": ["text", "status"]
}
```

| Campo | Tipo | Valores | Descripción |
|---|---|---|---|
| `input_mode` | string | `"text"` \| `"audio"` | Cómo enviará datos el cliente |
| `output_mode` | array | `"text"`, `"audio"`, `"status"` | Qué quiere recibir el cliente |

Si el handshake es inválido el servidor cierra con código **1008**.

### Ejemplos de handshake por caso de uso

```json
// Cliente de chat solo texto
{"input_mode": "text", "output_mode": ["text"]}

// Asistente de voz completo (micrófono + audio TTS + transcripción)
{"input_mode": "audio", "output_mode": ["audio", "text", "status"]}

// Voz de entrada, respuesta solo en texto
{"input_mode": "audio", "output_mode": ["text"]}

// Texto de entrada, respuesta en audio y texto
{"input_mode": "text", "output_mode": ["text", "audio"]}
```

---

## 2. Enviar texto

Tras el handshake (con cualquier `input_mode`), envía un mensaje JSON con el prompt:

```json
{"text": "¿Cuál es la capital de Francia?"}
```

Opcionalmente puedes especificar un modelo concreto:

```json
{"text": "Explícame la relatividad", "model_id": "gpt-4o"}
```

Si `text` está vacío o el JSON es inválido, recibirás un `error` y la conexión continúa.

---

## 3. Enviar audio de micrófono

Requiere `input_mode: "audio"` en el handshake.

Envía **frames binarios** de audio crudo en formato:

| Parámetro | Valor |
|---|---|
| Formato | PCM Float32 little-endian |
| Sample rate | 16000 Hz |
| Canales | 1 (mono) |

### Flujo de voz — review & send

> **Cambio importante desde v1.6.0:** la transcripción final ya **no se envía automáticamente al orquestador**. El cliente debe confirmarla explícitamente con `{"type": "send"}`. Esto permite que el usuario revise y corrija errores antes de procesar.

```
1. El cliente envía frames de audio PCM:
   → <PCM Float32 bytes>
   → <PCM Float32 bytes>

2. Llegan parciales opcionales mientras el transcriptor procesa:
   ← {"type": "transcription_partial", "text": "hola mun..."}

3. El cliente señaliza fin de audio:
   → {"type": "end"}

4. El transcriptor entrega la transcripción final — el cliente la muestra al usuario:
   ← {"type": "transcription", "text": "hola mundo"}

5. El usuario revisa/edita el texto. El cliente confirma enviando:
   → {"type": "send", "text": "hola mundo"}
      (puede ser diferente al original si el usuario lo corrigió)

6. El gateway lo envía al orquestador y llegan los tokens:
   ← {"type": "token", "content": "Hola, ¿en qué puedo ayudarte?"}
```

### Señal de fin de audio

Enviar `{"type": "end"}` es la forma de indicar al transcriptor que el usuario terminó de hablar y debe emitir la transcripción final. Úsalo cuando:

- El usuario suelta el botón de micrófono
- Detectas silencio prolongado en el cliente
- El VAD del cliente decide que la frase ha terminado

### Barge-in (interrumpir respuesta en curso)

Si el usuario empieza a hablar mientras llegan tokens del orquestador, el gateway detecta el nuevo audio y cancela el turno activo. Recibirás:

```json
{"type": "interrupted"}
```

Tras esto el flujo reinicia desde el paso 1 — sigue enviando audio normalmente.

---

## 4. Recibir texto de la IA

Requiere `"text"` en `output_mode`.

Los tokens llegan en streaming conforme el orquestador los genera:

```json
{"type": "token", "content": "La"}
{"type": "token", "content": " capital"}
{"type": "token", "content": " de Francia"}
{"type": "token", "content": " es París."}
```

Cuando la respuesta completa ha terminado:

```json
{"type": "end"}
```

**Pseudocódigo de recepción de texto:**

```python
buffer = ""
async for msg in ws:
    if isinstance(msg, str):
        data = json.loads(msg)
        if data["type"] == "token":
            buffer += data["content"]
            render(data["content"])          # actualiza UI en tiempo real
        elif data["type"] == "end":
            on_response_complete(buffer)
            buffer = ""
        elif data["type"] == "transcription":
            show_editable_transcription(data["text"])  # usuario puede editar
        elif data["type"] == "error":
            show_error(data["content"])
```

---

## 5. Recibir audio TTS

Requiere `"audio"` en `output_mode`.

Cuando el orquestador genera tokens, el gateway los envía en paralelo al TTS. El audio llega como **frames binarios** intercalados con mensajes JSON de control.

> Si el servicio TTS no está disponible, el gateway continúa en modo texto — los tokens llegan igualmente, solo sin audio.

### Flujo de mensajes por segmento sintetizado

```
← {"type": "audio_start", "chunk_id": 0}   JSON — nuevo chunk comenzando
← <bytes PCM16>                              binario — frames de audio
← <bytes PCM16>                              binario
← {"type": "audio_end", "chunk_id": 0}      JSON — chunk completo
← {"type": "audio_start", "chunk_id": 1}   JSON — siguiente frase
← <bytes PCM16>
← {"type": "audio_end", "chunk_id": 1}
← {"type": "end"}                            JSON — respuesta de texto completa
```

> `audio_start` / `audio_end` son informativos — puedes ignorarlos si solo necesitas el PCM. El `chunk_id` es un entero creciente desde 0 por sesión.

### Formato del audio recibido

| Parámetro | Valor |
|---|---|
| Formato | PCM16 (signed 16-bit, little-endian) |
| Sample rate | 24000 Hz |
| Canales | 1 (mono) |

### Pseudocódigo de recepción de audio

```python
async for msg in ws:
    if isinstance(msg, bytes):
        # Frame de audio PCM16 — reproducir o bufferizar
        audio_player.write(msg)
    elif isinstance(msg, str):
        data = json.loads(msg)
        match data["type"]:
            case "audio_start":
                pass  # opcional: preparar buffer para nuevo chunk
            case "audio_end":
                pass  # opcional: marcar fin de frase para sincronización
            case "end":
                audio_player.flush()
            case "transcription":
                show_editable_transcription(data["text"])
            case "error":
                handle_error(data["content"])
```

---

## 6. Mensajes de estado y error

### Estado (`"status"` en `output_mode`)

El orquestador emite eventos de estado internos durante el procesamiento (p.ej. llamadas a herramientas, cambios de modelo). Si incluyes `"status"` en `output_mode` los recibirás tal cual:

```json
{"type": "status", "content": "Buscando en base de datos..."}
```

### Estado de microservicios (`service_status`)

El gateway notifica degradaciones en los servicios internos:

```json
{"type": "service_status", "service": "tts", "status": "unavailable", "message": "..."}
```

| `service` | `status` | Impacto |
|---|---|---|
| `orchestrator` | `unavailable` | **Fatal** — la sesión se cierra, no hay respuesta posible |
| `transcriber` | `unavailable` | Degradado — sin transcripción, el cliente puede seguir en modo texto |
| `tts` | `unavailable` | Degradado — sin audio, los tokens de texto siguen llegando |

> Ante un `service_status` con `status: "unavailable"`, solo cierra la sesión si el servicio es `orchestrator`. Para `transcriber` y `tts` la sesión continúa en modo degradado.

### Errores (siempre enviados)

Los errores llegan independientemente de `output_mode`:

```json
{"type": "error", "content": "Descripción del problema"}
```

| Origen | Cuándo ocurre |
|---|---|
| Gateway | JSON inválido, fallo al conectar a microservicios |
| Orquestador | Error HTTP, timeout, fallo interno del modelo |
| TTS | `session_timeout`, `queue_full` (el audio de esa respuesta se pierde) |

Tras un error el gateway **no cierra la conexión** — puedes seguir enviando mensajes.

---

## 7. Modos de operación combinados

### Chat de texto puro

```
Cliente                         Gateway
   │── handshake ──────────────►│
   │   {"input_mode":"text",    │
   │    "output_mode":["text"]} │
   │                            │
   │── {"text":"Hola"} ────────►│──► Orchestrator
   │◄── {"type":"token","content":"Hola"} ──────────────────│
   │◄── {"type":"token","content":", ¿en qué puedo ayudar?"} │
   │◄── {"type":"end"} ─────────│
```

### Asistente de voz con review & send

```
Cliente                         Gateway
   │── handshake ──────────────►│
   │   {"input_mode":"audio",   │
   │    "output_mode":          │
   │     ["audio","text",       │
   │      "status"]}            │
   │                            │
   │── <PCM Float32 mic> ──────►│──► Transcriber
   │── <PCM Float32 mic> ──────►│
   │◄── {"type":"transcription_partial","text":"¿qué tiem..."} │
   │── {"type":"end"} ──────────►│   (usuario suelta el mic)
   │◄── {"type":"transcription","text":"¿qué tiempo hace?"} │
   │                            │   (usuario revisa, opcionalmente edita)
   │── {"type":"send",          │
   │    "text":"¿qué tiempo     │
   │           hace?"} ────────►│──► Orchestrator (streaming tokens)
   │                            │──► jota-speaker TTS (tokens en paralelo)
   │◄── {"type":"token","content":"Hoy"} ────────│
   │◄── {"type":"audio_start","chunk_id":0} ─────│
   │◄── <PCM16 audio> ──────────│
   │◄── {"type":"audio_end","chunk_id":0} ───────│
   │◄── {"type":"token","content":" hace sol."} ─│
   │◄── {"type":"audio_start","chunk_id":1} ─────│
   │◄── <PCM16 audio> ──────────│
   │◄── {"type":"audio_end","chunk_id":1} ───────│
   │◄── {"type":"end"} ─────────│
```

---

## 8. Referencia de mensajes

### Cliente → Gateway

| Mensaje | Formato | Cuándo |
|---|---|---|
| Handshake | `{"input_mode":"...", "output_mode":[...]}` | Primer mensaje, obligatorio |
| Fin de audio | `{"type":"end"}` | `input_mode="audio"` — usuario termina de hablar |
| Confirmar y enviar | `{"type":"send","text":"..."}` | Tras recibir `transcription` — lanza la respuesta del orquestador |
| Texto directo | `{"text":"...", "model_id":"..."}` | `input_mode="text"` — prompt directo, `model_id` opcional |
| Audio mic | frame binario PCM Float32 16kHz | `input_mode="audio"` |

### Gateway → Cliente

| Tipo | Formato | Condición |
|---|---|---|
| `transcription_partial` | `{"type":"transcription_partial","text":"..."}` | `input_mode="audio"`, parciales en tiempo real |
| `transcription` | `{"type":"transcription","text":"..."}` | `input_mode="audio"`, transcripción final — esperar `send` |
| `token` | `{"type":"token","content":"..."}` | `"text"` en `output_mode` — tras recibir `send` |
| `end` | `{"type":"end"}` | `"text"` en `output_mode`, fin de respuesta |
| `interrupted` | `{"type":"interrupted"}` | Barge-in confirmado — turno anterior cancelado |
| `audio_start` | `{"type":"audio_start","chunk_id":N}` | `"audio"` en `output_mode` |
| audio frame | frame binario PCM16 24kHz | `"audio"` en `output_mode` |
| `audio_end` | `{"type":"audio_end","chunk_id":N}` | `"audio"` en `output_mode` |
| `status` | `{"type":"status","content":"..."}` | `"status"` en `output_mode` |
| `service_status` | `{"type":"service_status","service":"...","status":"...","message":"..."}` | Siempre — degradación de microservicio |
| `error` | `{"type":"error","content":"..."}` | Siempre |
