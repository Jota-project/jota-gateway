# #130 S3a — Handlers de push fuera de `_listen`

Fecha: 2026-10-10. Estado: borrador pendiente de revisión.
Continúa [`2026-10-09-130-s1-outbound-serialization-design.md`](2026-10-09-130-s1-outbound-serialization-design.md).

## 1. Contexto y descomposición

El spec de S1 dejó S2 y S3 pendientes. Tras analizarlos se decide dividir el trabajo en tres
partes, cada una con su propio spec y plan, **en este orden**:

| Parte | Contenido | Depende de |
|---|---|---|
| **S3a** (este spec) | Handlers de push fuera de `_listen`: worker FIFO por bridge | — |
| **S2** | Política de cliente lento en `QueuedSender` | S3a |
| **S3b** | `TurnRegistry` con cola acotada y abort del turno lento | S3a, S2 |

**Por qué S3a va primero.** S2 hará que `send_*` pueda bloquear o fallar con cliente lento.
Cualquier llamador que siga ejecutándose dentro de `_listen` convertiría ese bloqueo en una
parada de **todas** las sesiones multiplexadas en la conexión de OpenClaw. Hoy esos llamadores
son los handlers de push.

**S3a corrige un problema que ya existe.** `_listen` hace `await dispatcher.dispatch(frame)` en
serie. `on_push_turn_end` espera inline la síntesis TTS completa (`await self._push_audio_task`,
`bridge.py` ~885), y `on_push_turn_start` hace `await self.tts.connect(...)`. Mientras dura, se
congela la lectura de frames de todas las sesiones.

### Dirección acordada para S2 y S3b (no se diseña aquí)

Resultados del spike de transporte (uvicorn 0.54.0, websockets 17.2, asyncio, loopback, cliente
que deja de leer; observado, no inferido):

- sansio y wsproto se comportaron igual: el buffer del transporte se acota en ~high-water
  (~64 KiB) y `await send` se cuelga indefinidamente.
- El ping timeout (~40 s) marca el cierre 1011 pero no libera el `send` ni la sesión.
- `close(1013)` con el buffer saturado también se cuelga; el código solo llega si el cliente
  vuelve a leer. `transport.abort()` es la única salida inmediata (el cliente ve EOF, 1006).
- `websocket._send.__self__` no da el transporte bajo Starlette. Sí un middleware ASGI puro que
  guarde `send.__self__` en `scope["state"]`.
- **Dependiente de versión:** en uvicorn 0.51.0 (la instalada localmente) sansio no define
  `pause_writing` y `send` no bloquea. `pyproject.toml` declara `uvicorn>=0.51.0` sin lockfile.
  Falta verificar desde qué versión bloquea sansio y fijar el mínimo.

Dirección: política en `QueuedSender` (timeout por `send` + vigilancia del buffer del transporte,
cubre sansio 0.51 y versiones recientes), salida con `close(1013)` de timeout corto y
`transport.abort()`, sin cambiar a wsproto y sin ack/crédito por ahora. El protocolo puede
cambiar si hiciera falta, con issues en los repos de cliente como dependencia coordinada.

## 2. Objetivo y criterios de éxito

Ningún handler de push puede retrasar la lectura de frames de OpenClaw ni, por tanto, a otras
sesiones.

- `dispatch()` retorna sin esperar IO de red ni de TTS para frames de push.
- El orden de eventos de push dentro de un bridge es idéntico al actual (total, FIFO).
- La semántica de #84 (colapso de starts, end huérfano) y de #112 (start suprimido con turno
  normal activo, end siempre reenviado) no cambia.
- Un TTS atascado solo afecta al push de su propio bridge y tiene tope de tiempo.

## 3. Diseño

### 3.1 Contrato dispatcher → bridge

- El dispatcher sigue decidiendo **de forma síncrona y en `_listen`** a quién va cada frame
  (`get_queue_by_session`, supresión de start #112). El timing de esa decisión no cambia.
- Donde hoy hace `await bridge.deliver_push(...)`, `on_push_turn_start`,
  `deliver_push_tool_call` u `on_push_turn_end`, pasa a llamar a
  `bridge.enqueue_push(kind, arg)`: **síncrono, no bloqueante**, sin `await`.
  `kind` ∈ `chat | tool | turn_start | turn_end`.
- Los métodos existentes conservan nombre y cuerpo y pasan a ser los **ejecutores** del worker.
  `tests/unit/test_bridge_push.py` sigue siendo válido.
- `end` se sigue reenviando siempre, sin condicionarlo al estado de `TurnRegistry`.

### 3.2 Worker FIFO por bridge

- `asyncio.Queue` por bridge, ilimitada en S3a (acotarla es S3b), y una única tarea consumidora.
- Arranque perezoso en el primer `enqueue_push`, mediante `_spawn`, para que `_supervise`
  registre cualquier crash. Los bridges sin push no pagan nada.
- Cada evento se ejecuta en `try/except Exception`: se registra el tipo de excepción (sin
  payload) y el worker continúa.
- Un único consumidor preserva el orden start → deltas → tool → end que necesitan
  `_push_turn_open` y el colapso de starts.

### 3.3 Acotar los bloqueos que hereda el worker

Con el worker serial, un TTS atascado bloquearía el push de ese bridge (no el de otros).

- Nuevo setting **`PUSH_TTS_DRAIN_TIMEOUT_S`** (default `30.0`) en `src/core/config.py`.
- En `on_push_turn_end`, `await self._push_audio_task` pasa a `asyncio.wait_for(...)` con ese
  timeout. Al vencer: cancelar la tarea de audio, cerrar el TTS y enviar `turn_end` igualmente.
- `tts.connect` ya está acotado por `TTS_AUTH_TIMEOUT_S`; no se toca.

### 3.4 Ciclo de vida y watchdogs

- `close_all`: marca el bridge como cerrado (`enqueue_push` pasa a no-op), cancela el worker
  **antes** de cerrar `_push_tts` y descarta lo pendiente. Debe seguir excluyendo
  `asyncio.current_task()` y awaitando lo que cancela, como el resto de tareas.
- El worker tolera un bridge ya desregistrado de `ClientRegistry` (#113).
- `_idle_watchdog`: un evento de push encolado y aún no procesado cuenta como actividad en
  vuelo, igual que `_push_turn_open` y `_active_turn`.

## 4. Pruebas

Nuevas:
- `dispatch()` de un frame `agent end` retorna sin esperar mientras `on_push_turn_end` espera un
  TTS lento (la prueba que hoy no existe).
- Orden FIFO bajo ráfaga de start/chat/tool/end.
- Un ejecutor que lanza no mata al worker; el siguiente evento se procesa.
- `close_all` cancela el worker y `enqueue_push` posterior es no-op.
- `PUSH_TTS_DRAIN_TIMEOUT_S`: al vencer se cancela el audio, se cierra el TTS y se envía
  `turn_end`.
- Un push encolado cuenta como en vuelo para el idle watchdog.
- #112 bajo concurrencia: la decisión sigue tomándose en `_listen` al llegar el frame.

Adaptadas: `tests/unit/test_openclaw_dispatcher.py` asume ejecución inline; pasa a comprobar
llamadas a `enqueue_push`, con la lógica de decisión intacta.

Intactas: `tests/unit/test_bridge_push.py`.

## 5. Fuera de alcance

- Cola de `TurnRegistry` acotada y abort del turno lento (S3b).
- Política de cliente lento y decisión de transporte (S2).
- Carrera entre dos llamadas concurrentes a `_cancel_active_turn()` y solape de un push abierto
  con un turno normal en el mismo socket: issues separadas, decisión del 2026-10-10.
- Tombstone de frames tardíos de un turno abortado (pertenece a S3b, que genera ese flujo).

## 6. Documentación a actualizar al cerrar

`CLAUDE.md` (secciones de push y `FrameDispatcher`, nuevo setting en variables de entorno),
`docs/ROADMAP.md` (#130) y el apartado de timeouts de `docs/client-protocol.md` si el
comportamiento visible cambia.

## 7. No verificado

- La versión de uvicorn que resolvería hoy el build de la imagen y desde cuándo sansio bloquea.
- Comportamiento en Linux/Docker y con uvloop (el spike fue macOS, asyncio puro).
- Que ningún test existente dependa del orden de ejecución inline más allá de los señalados.
