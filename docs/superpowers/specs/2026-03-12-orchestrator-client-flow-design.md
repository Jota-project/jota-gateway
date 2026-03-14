# Diseño: Flujo Cliente → Orquestador (streaming por partes)

**Fecha:** 2026-03-12
**Alcance:** Opción A — parche mínimo sobre el bridge existente
**Archivos afectados:** `src/models/schemas.py`, `src/services/bridge.py`

---

## Contexto

El gateway ya tiene la infraestructura base: WebSocket endpoint `/ws/stream/{client_id}`, `OrchestratorClient` con streaming NDJSON, y `JotaBridge` que coordina los servicios. Sin embargo, hay tres gaps concretos que impiden que el flujo funcione correctamente end-to-end:

1. `_client_input_loop` pasa texto crudo al orquestador (no parsea el JSON envelope del cliente).
2. `_on_token` envía `send_text(token_text)` — string crudo en vez de JSON estructurado.
3. No hay señal de fin de stream al cliente cuando el orquestador termina.

---

## Arquitectura

```
Cliente WS
  │  send_json({"text": "...", "model_id": "..."})
  ▼
JotaBridge._client_input_loop
  │  parsea ClientTextMessage; rechaza parse fallido y text vacío
  ▼
JotaBridge._call_orchestrator(text, model_id=None)
  │  OrchestratorClient.listen_loop(text, model_id=model_id, on_token=..., on_event=...)
  ▼
JotaOrchestrator POST /api/quick  (NDJSON streaming)
  │
  ├─► on_token  → send_json({"type": "token", "content": "..."})   [si "text" in output_mode]
  ├─► on_event  → send_json({...})  [si "status" in output_mode, EXCEPTO "error" que siempre pasa]
  └─► [fin]     → send_json({"type": "end"})                       [si "text" in output_mode]
```

---

## Componentes

### 1. `ClientTextMessage` (schemas.py)

Schema Pydantic nuevo para el mensaje de chat entrante del cliente:

```python
class ClientTextMessage(BaseModel):
    text: str
    model_id: Optional[str] = None
```

Nota: ya existe `OrchestratorControlMessage` en schemas.py, pero es para mensajes de control mid-session (cambio de modelo, etc.), no para prompts de usuario. `ClientTextMessage` es un schema separado y no debe consolidarse con el de control.

### 2. `_client_input_loop` (bridge.py)

Cambio en el bloque `elif "text" in message`. El valor `message["text"]` es un string JSON crudo (FastAPI/Starlette retorna el frame WebSocket sin parsear). Los pasos son:

1. `json.loads(message["text"])` para obtener un dict. Si lanza `json.JSONDecodeError`: `send_json({"type": "error", "content": "Mensaje inválido, se esperaba JSON"})` y `continue`.
2. `ClientTextMessage(**parsed_dict)`. Si lanza `ValidationError` (falta `text`, tipo incorrecto, etc.): `send_json({"type": "error", "content": "Mensaje inválido..."})` y `continue`. Nota: un `OrchestratorControlMessage` (`{"type": "...", "model_id": "..."}`) que llegue por este path también fallará aquí (sin campo `text`) y recibirá este error — comportamiento aceptable e intencional.
3. Si `msg.text.strip() == ""`: `send_json({"type": "error", "content": "El campo text no puede estar vacío"})` y `continue`. Validación explícita aquí, no en el schema.
4. Si válido: `await self._call_orchestrator(msg.text, model_id=msg.model_id)`.

**Guard de `input_mode`:** el path de texto está intencionalmente abierto a cualquier cliente, independientemente de `input_mode`. Un cliente de audio puede mandar texto y será procesado igual. Este comportamiento existía antes y se mantiene.

### 3. `_on_token` (bridge.py)

Cambio: reemplazar `send_text(token_text)` por `send_json({"type": "token", "content": token_text})`.

**El guard `if "text" in self.handshake.output_mode` se mantiene** — solo reciben tokens los clientes que lo solicitaron en el handshake.

### 4. Señal de fin de stream (bridge.py)

Al terminar `orchestrator.listen_loop`, emitir `send_json({"type": "end"})` **sólo si `"text" in self.handshake.output_mode`**.

Nota: cuando `output_mode` incluye `"audio"`, el fin del stream de texto no implica que el TTS haya terminado de enviar bytes; la coordinación TTS queda fuera del alcance de este diseño (iteración futura).

### 5. Paso de `model_id` (bridge.py)

- Firma: `async def _call_orchestrator(self, text: str, model_id: Optional[str] = None)`
- Cambio en la llamada a `listen_loop`: añadir `model_id=model_id` como kwarg (actualmente no se pasa). `listen_loop` ya acepta este parámetro en `orchestrator_client.py`.
- El call site desde `_on_transcribed_text` llama `await self._call_orchestrator(text)` — pasa `model_id=None` implícitamente, usa el modelo por defecto del orquestador. Sin cambios en esa ruta.

### 6. `_on_event` — bypass de errores (bridge.py)

Cambio: el guard actual `if "status" in self.handshake.output_mode` se reemplaza por:

```python
async def _on_event(data: dict):
    event_type = data.get("type")
    if event_type == "error" or "status" in self.handshake.output_mode:
        await self.client_ws.send_json(data)
```

Los errores siempre llegan al cliente. Los eventos de status solo si el cliente los solicitó.

---

## Formato de mensajes

### Cliente → Gateway
```json
{"text": "hola, cómo estás", "model_id": "gpt-4o"}
```
`model_id` es opcional.

### Gateway → Cliente

Los eventos se reenvían tal cual llegan del orquestador (sin normalización). Verificado empíricamente: el orquestador usa exclusivamente el campo `content` en todos sus eventos. El campo `message` en `OrchestratorResponse` es un artefacto del schema que no se emite en la práctica. El orquestador **no emite** un evento `{"type": "end"}` propio — el stream NDJSON simplemente termina; el `{"type": "end"}` al cliente lo genera el gateway.

| Tipo | Payload |
|------|---------|
| Token parcial | `{"type": "token", "content": "Hola"}` |
| Estado interno | `{"type": "status", "content": "..."}` |
| Error | `{"type": "error", "content": "..."}` |
| Fin de stream | `{"type": "end"}` (generado por el gateway, no el orquestador) |

---

## Manejo de errores

- **Parse fallido del mensaje del cliente:** `send_json({"type": "error", "content": "..."})`, el loop continúa.
- **Texto vacío (`text: ""`):** `send_json({"type": "error", "content": "El campo text no puede estar vacío"})`, el loop continúa. Validación en `_client_input_loop`, no en el schema.
- **Error del orquestador:** llega vía `_on_event` como `{"type": "error", "content": "..."}`. Bypasea el guard de `output_mode` — siempre se envía al cliente.
- **Desconexión del cliente:** `WebSocketDisconnect` capturado en `_client_input_loop`, termina el bridge limpiamente.

---

## Lo que NO cambia

- Flujo de audio (transcriber → orquestador) — no tocado.
- TTS — no tocado.
- Handshake — no tocado.
- `OrchestratorClient` — no tocado.
- Estructura de `JotaBridge` — sin refactor, cambios quirúrgicos.
