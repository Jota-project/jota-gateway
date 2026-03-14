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

Puedes enviar frames de cualquier tamaño. El transcriptor detecta automáticamente los finales de frase (VAD) y cuando obtiene una transcripción final:

1. El gateway te envía una confirmación:
   ```json
   {"type": "transcription", "text": "lo que dijiste"}
   ```
2. El texto se envía automáticamente al orquestador — no necesitas hacer nada más.

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
        elif data["type"] == "error":
            show_error(data["content"])
```

---

## 5. Recibir audio TTS

Requiere `"audio"` en `output_mode`.

Cuando el orquestador genera tokens, el gateway los envía en paralelo al TTS. El audio llega como **frames binarios** intercalados con mensajes JSON de control.

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
                # Opcional: preparar buffer para nuevo chunk
                pass
            case "audio_end":
                # Opcional: marcar fin de frase para sincronización
                pass
            case "end":
                # Respuesta completa
                audio_player.flush()
            case "error":
                handle_error(data["content"])
            case "transcription":
                show_transcription(data["text"])
```

### Interrumpir audio (barge-in)

Si el usuario empieza a hablar mientras el TTS está sonando:

1. Para la reproducción en el cliente.
2. Cierra la conexión WebSocket con código **1000**.
3. Abre una nueva conexión con un nuevo handshake.

La reconexión en LAN es típicamente < 100 ms.

---

## 6. Mensajes de estado y error

### Estado (`"status"` en `output_mode`)

El orquestador emite eventos de estado internos durante el procesamiento (p.ej. llamadas a herramientas, cambios de modelo). Si incluyes `"status"` en `output_mode` los recibirás tal cual:

```json
{"type": "status", "content": "Buscando en base de datos..."}
```

### Errores (siempre enviados)

Los errores llegan independientemente de `output_mode`:

```json
{"type": "error", "content": "Descripción del problema"}
```

| Origen | Cuándo ocurre |
|---|---|
| Gateway | JSON inválido, `text` vacío, fallo al conectar a microservicios |
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

### Asistente de voz completo

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
   │◄── {"type":"transcription","text":"¿qué tiempo hace?"} │
   │                            │──► Orchestrator (streaming tokens)
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
| Texto | `{"text":"...", "model_id":"..."}` | Enviar prompt, `model_id` opcional |
| Audio mic | frame binario PCM Float32 16kHz | `input_mode="audio"` |

### Gateway → Cliente

| Tipo | Formato | Condición |
|---|---|---|
| `token` | `{"type":"token","content":"..."}` | `"text"` en `output_mode` |
| `end` | `{"type":"end"}` | `"text"` en `output_mode`, fin de respuesta |
| `transcription` | `{"type":"transcription","text":"..."}` | `input_mode="audio"` |
| `audio_start` | `{"type":"audio_start","chunk_id":N}` | `"audio"` en `output_mode` |
| audio frame | frame binario PCM16 24kHz | `"audio"` en `output_mode` |
| `audio_end` | `{"type":"audio_end","chunk_id":N}` | `"audio"` en `output_mode` |
| `status` | `{"type":"status","content":"..."}` | `"status"` en `output_mode` |
| `error` | `{"type":"error","content":"..."}` | Siempre |
