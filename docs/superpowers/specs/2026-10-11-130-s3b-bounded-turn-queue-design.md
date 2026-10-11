# #130 S3b — Cola de turno acotada en `TurnRegistry`

Fecha: 2026-10-11. Estado: autorrevisado (sin aprobación interactiva; decisiones a revisar en §6).
Continúa S1 (salida serializada), S3a (push fuera de `_listen`) y S2 (cliente lento).

## 1. Problema

`TurnRegistry.register()` crea una `asyncio.Queue()` sin límite por turno y el dispatcher hace
`await q.put(...)`. Con cola ilimitada el `put` nunca bloquea, pero la memoria crece sin tope si el
consumidor (`stream_response` → `pipe_tokens`/TTS) se atasca. Si la cola se acota y se mantiene
`await put`, el `put` bloquearía `_listen` y congelaría **todas** las sesiones multiplexadas, que es
justo lo que S3a quitó para push. Criterio de aceptación de #130: "cola acotada con backpressure".

## 2. Diseño

- `TurnRegistry.register()` crea `asyncio.Queue(maxsize=N)`, `N = settings.TURN_QUEUE_MAX`
  (default 1000; el constructor admite override para tests).
- El dispatcher ya no hace `await q.put`: usa `TurnRegistry.deliver_by_session(sk, item)` /
  `deliver_by_req(req_id, item)`, síncronos y con `put_nowait`. Devuelven `True` si un turno es
  dueño de la sesión (aunque el item se descarte) y `False` si no hay turno (→ ruta de push).
- **Overflow** (`QueueFull`): se vacía la cola (los items pendientes pertenecen a un turno que se va
  a abortar), se encola un único `("overflow", None)` y la sesión se marca como desbordada: los
  frames siguientes de ese turno se descartan sin tocar la cola. Se registra un WARNING con el
  `session_key` y el tamaño (nunca contenido).
- `stream_response` trata `overflow` como `error` con contenido `turn_overflow` pero **sin marcar
  `_finished`**, de modo que su `finally` envía `chat.abort` (mismo camino que `turn_timeout`;
  trampa conocida: un `error` normal pone `_finished=True` y no abortaría). `call_orchestrator`
  convierte el `error` en `RuntimeError`, y el bridge lo emite como `TURN_ERROR` con
  `message:"turn_overflow"` (igual que `turn_timeout`: sin cambio de forma en el wire).
- **Lápida** (trampa conocida): tras abortar, los frames tardíos del turno llegarían con la
  `session_key` ya sin cola y se reencaminarían como push (abriendo un turno de push fantasma). Al
  desbordar se anota `session_key → now + TURN_ABORT_TOMBSTONE_S` (5 s, constante interna, no
  setting). `deliver_*` sigue devolviendo `False` pero el dispatcher consulta
  `TurnRegistry.is_aborted(sk)` y descarta `chat`/`tool`/`agent start` de esa sesión. `agent end`
  se sigue reenviando siempre (#112/#84: `on_push_turn_end` es no-op sin push abierto). La lápida
  caduca sola y se purga de forma perezosa.
- `error_all()` (cierre de conexión) no puede lanzar `QueueFull`: usa un `put` forzado (vacía si
  hace falta y encola el `error`).
- Un `done` (frame `res`) también pasa por `deliver_by_req`, con la misma política.

## 3. Interacción con otros mecanismos

- **#99/#150:** el `unregister` sigue en el `finally` de `stream_response`; el estado "desbordada"
  vive con la cola y se borra en `unregister` solo si `req_id` sigue siendo el dueño.
- **#115:** el timeout idle-reset no cambia; tras un overflow el siguiente `get` devuelve de
  inmediato el marcador.
- **Barge-in:** cancelar la tarea del turno ejecuta el mismo `finally` (unregister + `chat.abort`);
  no pone lápida (no es un overflow). Si coinciden overflow y barge-in, el `finally` es idempotente
  respecto al registro y solo puede enviar un abort duplicado (inocuo).
- **#201/#202:** no se tocan. La lápida no empeora #202 (solo descarta frames de un turno ya
  abortado); no se añade ningún `await` nuevo a `_cancel_active_turn`.

## 4. Qué ve el cliente

`{"type":"error","code":"TURN_ERROR","message":"turn_overflow","fatal":false,"turn_id":...}`; la
sesión sigue viva. Documentado en `docs/client-protocol.md` junto a `turn_timeout`.

## 5. Tests (TDD)

Registro: cola acotada, overflow vacía y deja un marcador, descartes posteriores, `unregister`
limpia, lápida con caducidad, `error_all` con cola llena. Dispatcher: overflow no bloquea y no
reencamina a push; frames tardíos descartados; `end` se sigue reenviando. Cliente: `turn_overflow`
produce `error` y envía `chat.abort`. Config/.env.sample.

## 6. Decisiones a revisar

1. **Tamaño 1000** eventos por turno (cada uno es un delta de texto o evento de herramienta,
   típicamente pocos bytes): un consumidor sano nunca lo acerca; solo lo alcanza un turno atascado.
2. Al abortar se **descartan** los tokens pendientes en cola (el cliente no los recibe) en vez de
   drenarlos: el turno se da por perdido.
3. Se reutiliza `TURN_ERROR`/`message` (sin código nuevo) para no obligar a cambios en clientes.
4. TTL de lápida 5 s fijo: cubre los frames en vuelo tras `chat.abort`; si OpenClaw tarda más en
   parar el turno, los frames posteriores podrían reencaminarse como push (limitación conocida).
