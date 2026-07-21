# Diseño: fix #114 — renombrar `ready.capabilities` y añadir `live_capabilities`

**Fecha:** 2026-07-21
**Rama base:** `phase/3-lifecycle`
**Rama de trabajo:** `fix/114-ready-capabilities-rename`

## Objetivo

Cerrar la contradicción que el cliente ve entre `ready.capabilities` y los mensajes `status` posteriores, separando de forma explícita la intención del handshake de la disponibilidad real de cada servicio.

## Decisión confirmada

ROADMAP (commit `8554148`):
- Renombrar `capabilities` → `requested_capabilities`.
- Añadir `live_capabilities` como instantánea tomada justo antes de enviar `ready`.
- No mantener alias del campo antiguo: cualquier cliente que aún espere `capabilities` debe actualizarse.
- `status` sigue siendo la fuente de cambios de disponibilidad tras el handshake; no se reenvía `ready` ni se añade un canal de actualización de capacidades.

## Causa raíz

`src/api/routes.py:115-132` construye `capabilities` a partir de la intención del handshake y de la configuración estática, ignorando los resultados de `JotaBridge.health_check()` (`src/services/bridge.py:183-218`). El cliente ve `tts: true` aunque TTS no responda al ping, y lo nota sólo cuando llega el `status` posterior.

## Diseño

### `JotaBridge.health_check()` (firma nueva)

```python
async def health_check(self) -> dict[str, bool] | None
```

- `None`: el orquestador no responde; el caller cierra el WS con 1011 y no envía `ready`. Antes de devolverlo se envía el `status` `orchestrator: unavailable` ya existente.
- `dict`: instantánea con tres claves (`barge_in`, `tts`, `transcriber`) y su valor live booleano. La rama no fatal ya no devuelve un `bool` opaco.

Construcción del diccionario (todos los campos se calculan una sola vez, sin pings duplicados):

| Clave | Live cuando |
|-------|-------------|
| `transcriber` | `handshake.input_mode == "audio"` y `self.transcriber` está `ConnectionState.CONNECTED`. |
| `tts` | `"audio" in handshake.output_mode` y `await TTSClient.ping(settings.TTS_WS_URL)` devuelve `True`. |
| `barge_in` | `config.barge_in_enabled` y el transcriber está conectado. |

Los `status` enviados en `health_check()` para transcriber/TTS no cambian: el cliente ve primero el aviso de indisponibilidad y, cuando llegue `ready`, ya dispone de `live_capabilities` para confirmar.

### `src/api/routes.py` (handshake)

```python
live = await bridge.health_check()
if live is None:
    await websocket.close(code=1011, reason="Servicio crítico no disponible.")
    return

await websocket.send_json({
    "type": "ready",
    "session_id": session_id,
    "agent": resolved_agent,
    "input_mode": handshake.input_mode,
    "output_mode": handshake.output_mode,
    "requested_capabilities": {
        "barge_in": config.barge_in_enabled,
        "tts": "audio" in handshake.output_mode,
        "transcriber": handshake.input_mode == "audio",
    },
    "live_capabilities": live,
})
```

No se emite la clave antigua `capabilities`. `live_capabilities` y `requested_capabilities` siempre se envían con las mismas tres claves, en el mismo orden.

### `docs/client-protocol.md`

- Tabla del mensaje `ready` actualizada: dos filas nuevas `requested_capabilities` y `live_capabilities`; se retira la fila `capabilities`.
- Párrafo introductorio: explica que `live_capabilities` es instantánea del handshake y que cualquier transición posterior se comunica vía `status`.
- Bloque "Incompatibilidades": una nota describiendo el cambio de contrato y la obligación de actualizar el cliente.

### `tests/e2e/ws_helpers.py`

`ws_handshake()` actualmente asume que el primer frame es `ready`. Cambia a:

1. Consumir frames hasta `type == "ready"` o agotar el timeout.
2. Si el primer frame es `status` u otro tipo, seguir iterando.
3. Si vence el timeout sin `ready`, cerrar el WS y lanzar un `AssertionError` con el último frame observado.

Mantiene la semántica de retorno y no rompe los tests existentes cuyo primer frame sigue siendo `ready`.

## Pruebas TDD

### Unit (`tests/unit/test_bridge_health_check.py`)

Reemplazar las aserciones `is True/False` por verificaciones del nuevo contrato:

- todos los servicios OK → `dict` con `barge_in, tts, transcriber` `True` en modo audio.
- TTS caído (`TTSClient.ping` patched a `False`) y `output_mode=["audio","text"]` → `tts: False`, `transcriber: True`, `barge_in: True`.
- Transcriber degradado → `transcriber: False`, `barge_in: False`.
- Handshake text-only → `dict` con las tres claves a `False`.
- Orquestador caído → `None` y `status` con `service: orchestrator, state: unavailable` enviado.

### Integración (`tests/integration/test_ws_handshake.py`)

- `ready` contiene `requested_capabilities` y `live_capabilities` con las tres claves; `capabilities` no aparece.
- Con TTS apagado (mock puntual) el handshake audio → `live_capabilities.tts == False`, `requested_capabilities.tts == True` y un `status` `tts: unavailable` precede al `ready`.
- Handshake text-only → ambas secciones coinciden y todas las claves `False`.

### Helper E2E

- Test que consume un `status` antes del `ready` y verifica que el helper lo trata como estado intermedio.
- Test que mantiene el comportamiento "primer frame es `ready`" cuando el servicio está sano.

## Commits y entrega

- Especificación committeada primero.
- Único commit de producción: `fix(#114): split ready capabilities into requested and live`, con todos los cambios y el trailer `Co-Authored-By: Claude <noreply@anthropic.com>`.
- `docs/ROADMAP.md`: marcar #114 `[x]`.
- Verificación final: `PYTHONPATH=. pytest`, `ruff check src/ tests/`, revisión global por subagente, push y PR contra `phase/3-lifecycle` con `Closes #114`.
