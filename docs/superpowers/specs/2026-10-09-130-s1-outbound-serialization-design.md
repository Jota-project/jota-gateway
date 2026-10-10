# Diseño: #130 · S1 — salida serializada al cliente (`OutboundSender`)

**Fecha:** 2026-10-09
**Rama de trabajo del spec:** `docs/130-s1-outbound-serialization-spec`
**Issue:** #130 (Fase 5). Este documento es **S1 de 3**; S2 y S3 tendrán su propio spec.

## Contexto y descomposición de #130

#130 mezcla tres problemas de peso distinto y se trata como tres sub-proyectos, cada uno con spec → plan → PR:

| Sub-proyecto | Alcance | Estado |
|---|---|---|
| **S1 (este spec)** | Un único camino de salida al cliente, FIFO, con un writer por sesión; todos los `status` por `notify_service_status`; drenado al cerrar. | Diseño |
| S2 | Detección y política de cliente lento (descartar / cerrar), documentada en `docs/client-protocol.md` §13. | Pendiente; depende de una decisión de transporte (ver "Fuera de alcance") |
| S3 | `TurnRegistry` con cola acotada (`put_nowait` + abort del turno lento) y handlers de push fuera de `_listen`. | Pendiente |

## Problema

Hay ~29 puntos de escritura al WebSocket del cliente (`bridge.py`, `routes.py`, `pipeline_tracker.py`). Los ejecutan, concurrentemente sobre el mismo `WebSocket`, al menos estas tareas: `_active_turn` (dos ramas de un `gather`: `pipe_tokens` y `pipe_audio`), `_push_audio_task`, la tarea `_listen` de OpenClaw (entrega de push), `transcriber_run` (callbacks), `transcription_watchdog`, tareas fire-and-forget de `status` (`main.py`, `bridge.py:_on_transcriber_state_change`) y `PipelineTracker.record()` desde casi cualquiera de ellas.

Verificado en el código instalado (starlette 1.0.0, uvicorn 0.51.0, websockets 16.0; el Dockerfile arranca `uvicorn` sin `--ws`, así que se usa el protocolo `sansio`):

- `starlette.WebSocket.send` no tiene lock.
- En `sansio`, `send` no tiene ningún `await` tras `await self.writable.wait()`, por lo que **cada mensaje es atómico**: no se corrompen ni se parten frames.
- Lo que **no** está determinado es el **orden entre mensajes de tareas distintas**: un `pipeline_event` o un `status` puede colarse entre `turn_start` y los primeros `token`, o un `turn_end` puede adelantar a un frame de audio de la otra rama.

## Objetivo

Que todo lo que el gateway envía a un cliente salga por **un único camino con orden total** (el orden de encolado), y que el cierre de la sesión no pierda el último mensaje.

## Decisiones confirmadas

1. **Writer como objeto inyectable (`OutboundSender`)**, creado en `routes.py` antes del tracker y del bridge y compartido por ambos.
2. **Tras el primer fallo de socket, los `send_*` siguientes lanzan `ClientGone`** (subclase de `ConnectionError`); los `except` actuales de los productores siguen sirviendo.
3. **`routes.py` es dueño del sender y lo drena en su `finally`**, entre `bridge.close_all()` y `websocket.close()`, con límite `SHUTDOWN_DRAIN_S` (sin setting nuevo).

## Diseño

### Módulo nuevo `src/services/outbound.py`

```python
class ClientGone(ConnectionError): ...

class OutboundSender(Protocol):
    async def send_json(self, payload: dict) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def flush(self) -> None: ...
    async def aclose(self, timeout: float) -> None: ...
```

- **`DirectSender(ws)`**: `send_json`/`send_bytes` hacen `await ws.send_*` directamente; `flush()` y `aclose()` son no-ops. Es el valor por defecto de `JotaBridge` y `PipelineTracker` (`DirectSender(client_ws)`), de modo que los tests existentes —que construyen el bridge con un `AsyncMock` y aseveran sobre `ws.send_json`— no cambian. Las sesiones HTTP de `/v1` usan `DirectSender(_NullWS())`.
- **`QueuedSender(ws)`**: una `asyncio.Queue` **ilimitada** y una tarea writer propia.
  - `send_*` encola `(kind, payload)` y vuelve. Si el sender está roto, lanza `ClientGone` en lugar de encolar.
  - El writer consume en orden y hace `await ws.send_*`. Ante `Exception` marca el sender como roto, descarta lo pendiente y registra **una** línea WARNING (sin payload).
  - `flush()` espera a que se vacíe lo encolado hasta ese momento y relanza `ClientGone` si hubo fallo.
  - `aclose(timeout)` deja de aceptar mensajes nuevos, drena con `asyncio.wait_for(..., timeout)` y para el writer. En timeout o con el socket roto, descarta lo pendiente y vuelve sin lanzar.
  - La tarea writer **no** está en `bridge.tasks`, así que `close_all()` no la cancela antes de tiempo.

### Garantía de orden

Todo mensaje —JSON o binario— sale en el orden en que **se encola**. En el bridge, `await send_*` termina cuando el mensaje ya está en la cola, así que el orden entre tareas queda fijado por quién llega antes a encolar. Un mensaje nunca se parte. No hay prioridades ni descartes en S1.

La cola es ilimitada a propósito: con `sansio` el writer la vacía al instante (el `send` no aplica backpressure), por lo que acotarla no detectaría nada. Eso es S2.

### Cambios en los productores

- `JotaBridge.__init__` y `PipelineTracker.__init__` reciben `sender: OutboundSender | None = None`; si es `None`, `DirectSender(client_ws)`. Se mantiene el parámetro `client_ws` (lo siguen usando los cierres y `ClientRegistry`).
- Los ~22 `client_ws.send_json/send_bytes` de `bridge.py` y el de `pipeline_tracker.py:77` pasan a `self._sender.send_*`. La lógica de `try/except` de cada sitio **no cambia**.
- **Todos los `status` por `notify_service_status`:** se migran el `degraded` del watchdog de silencio (`bridge.py:384`) y los `status` unavailable del health check (`bridge.py:274/291/304`). El health check conserva su semántica de propagar el error de envío.

### `routes.py`

Orden de construcción: `QueuedSender(websocket)` → `PipelineTracker(sender=...)` → `JotaBridge(sender=...)`.

- Tras enviar `ready` se hace `await sender.flush()`: un fallo se trata como hoy (warning y cierre).
- `finally`: `await bridge.close_all(...)` → `await sender.aclose(settings.SHUTDOWN_DRAIN_S)` → `websocket.close()`.
- Los cierres previos al bridge (1008 del handshake) no usan el sender.

### Interacción con el cierre

- `close_all()` no cambia: sigue idempotente y serializado por `_close_lock`, sin saber que existe un writer. `tracker.close()` encola `session_end`, y el drenado posterior de `routes.py` lo entrega.
- El drenado de shutdown (`ClientRegistry.close_all_sessions`) sigue llamando a `bridge.close_all()`; el sender se drena cuando el endpoint llega a su `finally`.

## Migración por pasos (suite verde en cada paso)

1. Crear `outbound.py` con `DirectSender`, `QueuedSender`, `ClientGone` y sus tests unitarios.
2. `JotaBridge` y `PipelineTracker` reciben el sender (por defecto `DirectSender`); migrar los puntos de envío.
3. `routes.py`: construcción sender → tracker → bridge con `QueuedSender`, `flush()` tras `ready` y `aclose()` en el `finally`.
4. Unificar los `status` restantes en `notify_service_status`.
5. Documentación: `CLAUDE.md` (módulo, orden de construcción, garantía) y `docs/client-protocol.md` (orden total de salida; sin cambio de formato de mensajes).

## Tests

- `QueuedSender`: orden FIFO con N tareas concurrentes; JSON y binario intercalados conservan el orden de encolado; tras un fallo de socket el siguiente `send_*` lanza `ClientGone` y lo pendiente se descarta; `flush()` relanza el fallo; `aclose()` drena y respeta el timeout, y no espera con el socket roto.
- Bridge: un `turn_end` encolado justo antes del cierre llega al cliente (`close_all` → `aclose`); con el sender roto, `pipe_audio` y `_pipe_push_audio` dejan de producir.
- Integración con `TestClient`: se mantienen `status*` → `ready` → `turn_start` → `token`, y `pipeline_event` no se cuela entre `turn_start` y el primer evento del turno que lo causó.
- Los tests existentes deben pasar sin modificación (usan `DirectSender`).

## Riesgos conocidos

- **`broadcast_status` entre el registro y `ready`:** `connect_internal_services()` registra el bridge en `ClientRegistry` antes de que `routes.py` envíe `ready`, por lo que un `status` de otra tarea podría adelantarse a `ready`. S1 no lo cambia; el orden total solo garantiza coherencia, no que `ready` sea el primero.
- **Latencia de detección de socket muerto:** el productor se entera un mensaje más tarde (el que falló ya se dio por encolado).
- **Memoria con cliente lento:** la cola ilimitada se vacía al instante en `sansio`, pero el buffer del transporte sigue sin límite. Es exactamente lo que S2 debe tratar.

## Fuera de alcance

- **S2:** detección y política de cliente lento. Con `sansio` el `send` no bloquea (`pause_writing`/`resume_writing` no están definidos; `wsproto` sí los define pero no es el protocolo por defecto), así que S2 debe decidir entre `--ws wsproto`, leer el buffer del transporte, o un mecanismo de ack/crédito.
- **S3:** `TurnRegistry` acotado y handlers de push fuera de `_listen` (hoy `deliver_push`, `on_push_turn_start` y `on_push_turn_end` se esperan inline dentro de `_listen`).
- Issues aparte: dos llamadas concurrentes a `_cancel_active_turn()` pueden pisarse; un push abierto y un turno normal pueden solaparse en el mismo socket.

## No verificado

- Cómo se comporta producción con otro protocolo WS: se asumió `sansio` por el Dockerfile; no se inspeccionó la imagen.
- Si uvicorn modifica los límites del buffer de escritura del transporte.
- El orden exacto entre `ClientRegistry.register` y el envío de `ready` se dedujo de `bridge.py` y `routes.py`, sin una prueba que lo reproduzca.
- Los recuentos de tests afectados (~60 aserciones, ~15 archivos) son de `grep`, no de una ejecución.
