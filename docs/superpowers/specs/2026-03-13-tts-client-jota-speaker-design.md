# Diseño: Integración TTSClient con jota-speaker (puerto 8005)

**Fecha:** 2026-03-13
**Alcance:** Adaptar `TTSClient` al protocolo WebSocket de jota-speaker y refactorizar `JotaBridge` para ciclo de vida TTS por petición.
**Archivos afectados:** `src/services/tts_client.py`, `src/core/config.py`, `src/services/bridge.py`

---

## Contexto

El `TTSClient` existente implementa un protocolo simple y obsoleto: envía texto crudo al WS y lee bytes crudos de vuelta. El nuevo servicio `jota-speaker` (puerto 8005) define un protocolo estructurado con autenticación, mensajes JSON de control y frames binarios PCM16 intercalados.

Además, el modelo de sesión de jota-speaker es **una sesión por conexión WS**: el servidor cierra la sesión tras recibir `end` y emitir `done`. El bridge actual mantiene un `TTSClient` persistente como tarea de fondo, lo que es incompatible con este modelo.

---

## Protocolo jota-speaker (resumen)

```
Client                              Server
  │──── WS connect ─────────────────►│
  │──── {"type":"auth","token":"…"} ─►│
  │◄─── {"type":"auth_ok"} ──────────│
  │──── {"type":"token","text":"…"} ─►│  (repetir por cada token LLM)
  │──── {"type":"end"} ──────────────►│
  │◄─── {"type":"audio_start",…} ────│
  │◄─── <binary PCM16 frames> ───────│
  │◄─── {"type":"audio_end",…} ──────│
  │◄─── {"type":"done"} ─────────────│
  │──── WS close (1000) ─────────────►│
```

---

## Arquitectura

```
_call_orchestrator(text)
  │
  ├─ [si "audio" in output_mode]
  │    TTSClient.connect()   ← nueva conexión + auth por petición
  │    asyncio.gather(
  │      pipe_tokens():
  │        orchestrator.listen_loop() → on_token → tts.send_text_chunk()
  │        [fin orquestador] → tts.end()
  │      ,
  │      pipe_audio():
  │        tts.get_audio_stream() → client_ws.send_bytes()
  │    )
  │    TTSClient.close()
  │
  └─ [si NO "audio" in output_mode]
       orchestrator.listen_loop() → on_token → on_event
```

La ruta de texto (`"text" in output_mode`) y la ruta de audio (`"audio" in output_mode`) son independientes dentro de `on_token`. El TTS solo se instancia si el cliente solicitó audio.

---

## Componentes

### 1. `src/services/tts_client.py` — reescritura

**Interfaz pública:**

```python
class TTSClient:
    def __init__(self, url: str, token: str, client_id: str)
    async def connect() -> None
    async def send_text_chunk(text: str) -> None
    async def end() -> None
    async def get_audio_stream() -> AsyncGenerator[bytes, None]
    async def close() -> None
```

**Comportamientos:**

- `connect()`: abre WS, envía `{"type":"auth","token":"<token>"}`, espera la próxima respuesta. Si llega `auth_ok` continúa. Si llega `auth_error`, `ConnectionClosed` (el servidor cierra con 1008 justo después de `auth_error`), o cualquier otro tipo de mensaje inesperante, lanza `RuntimeError`.
- `send_text_chunk(text)`: envía `{"type":"token","text":text}`. Si el WS está cerrado loguea warning y retorna silenciosamente — la contraparte `get_audio_stream()` detectará `ConnectionClosed` y saldrá limpiamente.
- `end()`: envía `{"type":"end"}`. Si el WS está cerrado loguea warning y retorna silenciosamente — misma cadena de cleanup que `send_text_chunk`.
- `get_audio_stream()`: itera el WS. Frames binarios → yield. Frames JSON:
  - `audio_start` / `audio_end` → ignorados (loguea a nivel DEBUG).
  - `done` → break (fin del generador).
  - `error` → loguea warning con `code` y `message`, break.
  - `ConnectionClosed` → termina el generador.
- `close()`: no-op si `self.ws is None` (p.ej. si `connect()` falló antes de asignar). Si `self.ws` existe y no está cerrado, lo cierra con código 1000.

### 2. `src/core/config.py` — dos cambios

```python
TTS_WS_URL: str = "ws://localhost:8005/ws"
TTS_TOKEN: str = "gateway"
```

> El stub de jota-speaker acepta **cualquier token no vacío**. `"gateway"` funciona en local sin configuración adicional. En producción (`jota_db`) los operadores deben sobreescribir `TTS_TOKEN` en `.env` con el token real registrado en jota-db.

### 3. `src/services/bridge.py` — cambios quirúrgicos

**Eliminar:**
- Campo `self.tts: Optional[TTSClient]`
- Bloque TTS en `connect_internal_services` (instanciación + `connect_tasks.append`)
- `close_all`: línea `if self.tts: close_aws.append(self.tts.close())`
- Tarea `_tts_to_client_loop` en `run()`
- Método `_tts_to_client_loop`

**Modificar `_call_orchestrator`:**

```python
async def _call_orchestrator(self, text: str):
    needs_audio = "audio" in self.handshake.output_mode

    tts: Optional[TTSClient] = None
    if needs_audio:
        tts = TTSClient(
            url=settings.TTS_WS_URL,
            token=settings.TTS_TOKEN,
            client_id=self.client_id,
        )
        await tts.connect()

    async def _on_token(token_text: str):
        if "text" in self.handshake.output_mode:
            await self.client_ws.send_json({"type": "token", "content": token_text})
        if tts:
            await tts.send_text_chunk(token_text)

    async def _on_event(data: dict):
        if data.get("type") == "error" or "status" in self.handshake.output_mode:
            await self.client_ws.send_json(data)

    async def pipe_tokens():
        await self.orchestrator.listen_loop(text=text, on_token=_on_token, on_event=_on_event)
        if tts:
            await tts.end()

    async def pipe_audio():
        async for chunk in tts.get_audio_stream():
            await self.client_ws.send_bytes(chunk)

    if tts:
        try:
            await asyncio.gather(pipe_tokens(), pipe_audio())
        finally:
            await tts.close()
    else:
        await pipe_tokens()

---

## Manejo de errores

| Situación | Comportamiento |
|---|---|
| `auth_error` de jota-speaker | `connect()` lanza `RuntimeError` → `_call_orchestrator` propaga → `_client_input_loop` lo captura y loguea; la respuesta al cliente falla sin audio pero el bridge sigue vivo |
| `error` mid-stream (`session_timeout`, `queue_full`) | `get_audio_stream()` loguea warning y termina el generador limpiamente |
| `ConnectionClosed` en `send_text_chunk` / `end` | Se loguea warning y retorna sin relanzar |
| `ConnectionClosed` en `get_audio_stream` | Termina el generador |

---

## Cambio intencional en `_on_event`

La nueva `_on_event` envía errores del orquestador al cliente **incondicionalmente**, independientemente de `output_mode`. Esto es un cambio respecto al código actual (que solo reenvía si `"status" in output_mode`). El criterio: los errores siempre deben llegar al cliente para que pueda reaccionar. Esta lógica ya fue aprobada en el spec anterior (2026-03-12).

---

## Lo que NO cambia

- Handshake, transcriber, `OrchestratorClient`
- El cliente físico sigue recibiendo bytes PCM16 crudos (no se reenvían `audio_start`/`audio_end`)
- Estructura general de `JotaBridge` — cambios quirúrgicos, sin refactor mayor
