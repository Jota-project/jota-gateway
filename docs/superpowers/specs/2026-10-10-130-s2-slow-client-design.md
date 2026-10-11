# #130 S2 — Política de cliente lento

Fecha: 2026-10-10. Estado: borrador pendiente de revisión.
Continúa [`2026-10-09-130-s1-outbound-serialization-design.md`](2026-10-09-130-s1-outbound-serialization-design.md) y
[`2026-10-10-130-s3a-push-worker-design.md`](2026-10-10-130-s3a-push-worker-design.md) (PR #198, mergeado).

## 1. Contexto

S1 serializó la salida al cliente (`QueuedSender`) y dejó la cola ilimitada a propósito: se
creía que con el protocolo `sansio` de uvicorn el `send` no aplicaba backpressure, así que
acotar la cola no detectaría nada. S3a sacó los handlers de push de `_listen`, de modo que
ningún productor del bridge espera ya a un `send` lento: todos encolan con `put_nowait`. Lo
único que puede bloquearse es la tarea writer de `QueuedSender`, y eso es lo que S2 acota.

### Qué se midió (no se infiere)

Spike con uvicorn 0.54.0 y websockets 17.2 (asyncio puro, macOS, loopback), cliente que
deja de leer, 400 KB/s:

- sansio y wsproto se comportan igual: el buffer del transporte se acota en ~64 KiB
  (high-water) y `await send` se cuelga indefinidamente (78 s observados).
- El ping timeout (~40 s) marca el cierre 1011 pero no libera el `send` ni la sesión.
- `close(code)` con el buffer saturado también se cuelga; el código solo llega si el cliente
  vuelve a leer. `transport.abort()` es la única salida inmediata: el cliente ve EOF sin
  close frame (1006) y el `receive()` del servidor devuelve `websocket.disconnect`.
- `websocket._send.__self__` no da el transporte bajo Starlette. Sí un wrapper ASGI puro que
  guarde `send.__self__` en `scope["state"]`.

### Dependencia de versión (verificada)

El backpressure de sansio **no existe antes de uvicorn 0.52.1**. Se comprobó descargando las
ruedas: `pause_writing`/`resume_writing` están ausentes en 0.51.0 y 0.52.0 y presentes desde
0.52.1 (nota de release: "Add missing write flow control to the `websockets-sansio`
implementation", #3048; también #3050 "connection loss while a write waits on backpressure" y
#3053, cierre iniciado por el servidor espera la respuesta del cliente hasta 10 s).
`pyproject.toml` declaraba `uvicorn>=0.51.0` sin lockfile; hoy `pip` resuelve 0.54.0 y
websockets 17.2, pero un build o un entorno local podía caer en una versión sin backpressure.

## 2. Decisiones

Tomadas con el usuario el 2026-10-10:

1. **Subir el mínimo a `uvicorn>=0.52.1`.** S2 se apoya en el backpressure de uvicorn. No se
   añade vigilancia del tamaño del buffer del transporte: uvicorn ya lo acota en ~64 KiB y la
   señal es "este `send` lleva más de T segundos bloqueado".
2. **Timeout de 10 s por envío, intento de 1013 y `abort()`.** Sin ack/crédito (sin cambio de
   protocolo) y sin descartar frames.

## 3. Objetivo y criterios de éxito

Un cliente que no consume lo que se le envía no puede mantener viva su sesión indefinidamente
ni acumular memoria sin límite.

- Un `send` que lleva más de `CLIENT_SEND_TIMEOUT_S` bloqueado termina la conexión.
- El timeout es **por envío**, no acumulado: un cliente lento que avanza no se corta.
- La terminación reutiliza el teardown existente; no hay un camino de cierre nuevo.
- Sesiones sin cliente lento: comportamiento idéntico al actual.

**Alcance real de la señal.** El timeout es un umbral de caudal de drenado, no un detector de
"solo clientes parados". Con el write flow control de uvicorn un `send` espera desde el
high-water mark (~64 KiB) hasta el low-water (~16 KiB): ~48 KiB por ciclo, tiempo ≈ 48 KiB /
caudal de lectura del cliente. Teoría con `CLIENT_SEND_TIMEOUT_S=10`: se corta a quien drena por
debajo de ~5 KB/s. Medido en macOS loopback contra uvicorn real, con el gateway produciendo más
rápido de lo que lee el cliente: T=1s cortó a 10, 30, 60 y 100 KB/s (400 KB/s no); T=10s cortó
a 3 KB/s (a los 10.0 s), 8 KB/s (12.8 s) y 20 KB/s (11.2 s). La ventana TCP del kernel sube el
umbral real: del orden de unos pocos KB/s (teoría) a unas pocas decenas de KB/s (medidas), según
kernel y tasa de producción. Para escala, TTS PCM16 a 24 kHz son 48 KB/s. Los clientes por encima
del umbral no se cortan y la cola ilimitada sigue creciendo para ellos mientras dure la
producción: el criterio "ni acumular memoria sin límite" se cumple solo para clientes por debajo
del umbral. No es un limitador de caudal.

## 4. Diseño

### 4.1 Señal y política en `QueuedSender` (`src/services/outbound.py`)

- El writer envuelve cada `self._ws.send_json/send_bytes` en
  `asyncio.timeout(settings.CLIENT_SEND_TIMEOUT_S)`.
- Al vencer, el fallo es `ClientSlow(ClientGone)` (clase nueva) y se reutiliza el camino de
  fallo existente: se guarda `_failure`, `_discard_pending()` descarta lo encolado (los
  `flush()` en espera reciben `ClientGone`) y el writer termina. Los productores no cambian:
  `send_*` ya lanza `ClientGone` y ellos lo tragan.
- Una línea de log en WARNING con solo el tipo y el tiempo, nunca el payload.
- `QueuedSender.__init__` recibe `on_slow: Callable[[], Awaitable[None]] | None = None`. Se
  invoca una vez, después de descartar pendientes. Una excepción de `on_slow` se registra y no
  rompe el teardown del writer.
- `DirectSender` (sesiones HTTP de `/v1`, `_NullWS`) no cambia.

### 4.2 Terminar la conexión (`src/api/routes.py`)

`routes.py` construye `on_slow` al crear el `QueuedSender`. Hace, en orden:

1. Registrar un evento `client_slow` en el tracker (solo `timeout_s`, nunca texto de usuario).
2. Intentar `websocket.close(code=1013, reason=...)` acotado a 2 s (`_SLOW_CLOSE_GRACE_S`,
   constante, no setting): si el cliente reanuda la lectura en ese margen, ve 1013 (mejor esfuerzo: `abort()` corre justo tras un close correcto y descarta el buffer
   de escritura en espacio de usuario, así que un close frame encolado detrás de datos pendientes
   puede perderse; quien reanuda la lectura dentro del margen suele, no siempre, ver 1013).
3. `abort()` del transporte, esté o no entregado el close.

Tras el `abort()`, el `receive()` del bucle de entrada devuelve `websocket.disconnect`,
`run()` termina y el teardown de siempre sigue sin cambios (`close_all`, desregistro,
`sender.aclose`, que vuelve de inmediato porque el writer ya terminó). El `finally` de
`routes.py` solo llama a `websocket.close()` si `"DISCONNECTED" not in websocket.client_state.name`;
se omite porque el `receive()` del bridge vio la desconexión, y donde `client_state` siga en
CONNECTED, `close()` lanza un `RuntimeError` que el `except Exception: pass` existente traga.

Lo que ve el cliente: **1013** ("Try Again Later") si se recupera dentro de los 2 s; **1006**
(cierre anómalo) si no lee.

### 4.3 Acceso al transporte (`src/core/transport.py`, nuevo)

- Middleware ASGI puro `TransportCaptureMiddleware`, registrado junto a `RequestIdMiddleware`
  con `app.add_middleware`. Para `scope["type"] == "websocket"` guarda en
  `scope["state"]["transport"]` el `send.__self__.transport` del `send` crudo, que en uvicorn
  es un método enlazado al protocolo.
- `abort_client_transport(websocket) -> bool` lee ese valor y llama a `abort()`; devuelve
  `False` si no hay transporte.
- Si el `send` no es un método enlazado (`TestClient`, otro servidor ASGI) el valor queda
  `None`: se cierra con `close`, se registra un WARNING y nunca falla.

### 4.4 Dependencia y configuración

- `pyproject.toml`: `uvicorn>=0.52.1`.
- Setting `CLIENT_SEND_TIMEOUT_S: float = 10.0` en `src/core/config.py`, `.env.sample` y
  `CLAUDE.md`.
- Test unitario de guarda: `WebSocketsSansIOProtocol` define `pause_writing` y
  `resume_writing`. Falla si alguien baja el mínimo o cambia de implementación sin saberlo.
- `docs/client-protocol.md` §13: fila para `CLIENT_SEND_TIMEOUT_S` y el significado del
  código de cierre 1013.

## 5. Pruebas

Unitarias (`QueuedSender` con un ws falso cuyo `send` se bloquea con un `Event`, y
`CLIENT_SEND_TIMEOUT_S` reducido con `monkeypatch`):
- `send` bloqueado → `ClientSlow`, `on_slow` una sola vez, pendientes descartados, `flush()` y
  envíos posteriores lanzan `ClientGone`.
- Un `send` rápido no se ve afectado.
- Muchos envíos, cada uno por debajo del tope pero con suma mayor que él, no disparan nada.
- Un `on_slow` que lanza no rompe el teardown ni deja la tarea writer sin terminar.
- El log del fallo no contiene el payload.

Middleware (`src/core/transport.py`): con un `send` enlazado devuelve el transporte; con uno no
enlazado devuelve `None` y `abort_client_transport` devuelve `False`.

Integración con **uvicorn real** (servidor en un hilo, cliente de socket crudo que no lee,
`CLIENT_SEND_TIMEOUT_S=1`, chunks grandes para llenar el buffer rápido): la sesión termina en
pocos segundos y el bridge queda cerrado. Es la prueba que cubre toda la cadena, incluida la
captura del transporte por el middleware.

## 6. Fuera de alcance

- Descartar o coalescer frames (audio, `transcription_partial`, `pipeline_event`).
- Ack/crédito y cualquier cambio de protocolo con los clientes.
- Cola acotada del `TurnRegistry` y abort del turno lento (S3b).
- Tope de tiempo de `send_text_chunk` y `end()` del TTS de push (issue #199).
- Sesiones HTTP de `/v1` (`DirectSender`).
- Los `ws_ping_*` de uvicorn (ya activos; no se tocan).

## 7. Documentación a actualizar al cerrar

`CLAUDE.md` (sección de salida serializada: "Out of scope (S2, S3)" y el párrafo de
`QueuedSender`; variables de entorno), `docs/ROADMAP.md` (#130), `docs/client-protocol.md` §13
y `.env.sample`.

## 8. No verificado

- Linux/Docker, uvloop y Python 3.14 (el spike fue macOS, asyncio puro, Python 3.12).
- Que el middleware reciba el `send` crudo de uvicorn al registrarse con `app.add_middleware`
  (se razona por el orden de Starlette, pero solo la prueba con uvicorn real lo confirma).
- Que un cliente que reanuda la lectura dentro de los 2 s de margen reciba el 1013 (se midió
  que el close llega al reanudar la lectura, no con este margen concreto).
- Comportamiento con muchas conexiones simultáneas y con cliente muerto por caída de red sin
  ACK (el kernel acabaría cerrando por retransmisión TCP; no se probó).
- Si 10 s es el valor adecuado para clientes en redes inestables (ESP32 en Wi-Fi débil): es
  configurable, pero el default no se ha contrastado con tráfico real.
