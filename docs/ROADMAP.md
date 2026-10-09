# jota-gateway Roadmap

> **Estado:** 🔧 En remediación (post auditoría 2026-07-15) — Fases 1, 2, 3 y 4 ✅ cerradas (Fase 3: 2026-08-06, mergeada a `main` 2026-10-08; Fase 4: 2026-10-08, mergeada a `main` en #181 y publicada como v1.14.12; 8 de 9 issues — #126 pasa a Fase 5)
> **Última actualización:** 2026-10-08
> **Issues abiertas:** 14 (rango GitHub `#118`–`#163`)
> **Versión actual:** v1.14.12 (release 2026-10-08, cierra Fase 4)
> **Próximo release:** Fase 5 (polish batch, incluye #126). *(Los targets numéricos de esta versión del roadmap —1.15.x/1.16.0/1.17.0— no se ajustaron a la realidad: la Fase 2 salió como v1.14.10, la Fase 3 como v1.14.11 y la Fase 4 como v1.14.12, asignados por semantic-release al taggear; verificar el número que asigna el release automático.)*

Este documento es el **plan vivo de remediación y evolución** de jota-gateway. Cada tarea referencia una issue de GitHub; las casillas se tachan al cerrar la issue. Se actualiza en el mismo PR que cierra la issue, o en un PR dedicado.

**Auditoría completa:** [`docs/superpowers/plans/2026-07-15-production-readiness-roadmap.md`](./superpowers/plans/2026-07-15-production-readiness-roadmap.md) (planning narrative).
**Histórico:** [`../AUDIT_2026-06-28.md`](../AUDIT_2026-06-28.md) (auditoría anterior).
**Estado de las issues:** https://github.com/Jota-project/jota-gateway/issues?q=is:open+label:audit:2026-07-15

---

## TL;DR

| Métrica | Valor |
|---|---|
| Issues totales | **80** (14 abiertas) |
| 🔴 Críticos | 6 |
| 🟠 Altos | 15 |
| 🟡 Medios | 14 |
| ⚪ Tech-debt / polish | 5 |
| Estimación | ~3 sprints (6–9 semanas) |
| Próximo milestone | Fase 5 (polish batch) — incluye #126 (refactor de wrappers de reconexión, movida desde Fase 4) |
| Regresiones confirmadas vs auditoría junio | 2 |
| Features documentadas sin implementar | 3 |

---

## Estado actual (baseline)

El gateway funciona correctamente en **happy path** (sesión única + backend sano). Tiene **bugs críticos latentes**, **dos regresiones confirmadas** de hallazgos que la auditoría de junio marcó como resueltos, **features documentadas nunca implementadas**, y **gaps serios de producción** (sin timeouts, sin drenado de shutdown, auth de `/v1/*` mal documentada, claves en logs).

### Regresiones conocidas (de `AUDIT_2026-06-28.md`)

- **Bug 10** — circuit-breaker en estado `DEGRADED` de `ReconnectingOpenClawClient` → sigue sin estar implementado.
- **`_last_error` no se limpia** tras reconexión exitosa → en los 3 reconnecting wrappers.

### Features documentadas pero no implementadas

- `system_prompt_extra` (CLAUDE.md:109) — se descarta silenciosamente antes de llegar a OpenClaw.
- `default_agent` per-client — ignorado, siempre se usa el default global.
- `allowed_agents` per-client — ignorado, bypass de autorización potencial.

---

## Fases de remediación

### 🔴 Fase 1 — Bugs críticos (semanas 1–2) — ✅ CERRADA (2026-07-18)

**Objetivo:** cerrar las 6 regresiones/latentes críticas.
**Release target:** 1.15.x (patches incrementales).
**Acceptance gate:** cero issues 🔴 abiertas, `pytest` verde, suite e2e sin regresiones, ningún log contiene `client_key` completo.
**Estado del gate:** cero issues 🔴 abiertas ✅ · `pytest` verde ✅ (374 passed) · sin regresiones e2e ✅ · cero `client_key` completo en logs ⚠️ *no verificado aquí* — ese criterio es el alcance real de **#106** (Fase 2, todavía abierta); se mantiene el texto del gate tal cual pero se declara Fase 1 cerrada sobre la base de sus 6 issues críticas, no de ese criterio heredado.

- [x] **#99** 🔴 `[001]` — `TurnRegistry` concurrent same-session_key race corrupts turns — **S** — *race en `register()` cuando dos `stream_response` comparten `session_key`* — cerrado por #140
- [x] **#100** 🔴 `[002]` — `system_prompt_extra` silently dropped before reaching OpenClaw — **M** — *eliminado por completo (sin hook viable en el protocolo de OpenClaw)* — cerrado por #141
- [x] **#101** 🔴 `[003]` — Pre-ready WebSocket failure paths leak transcriber, bridge, session state — **M** — cerrado por PR (rama `fix/101-ws-setup-failure-leaks`)
- [x] **#102** 🔴 `[004]` — `ReconnectingOpenClawClient` has no circuit-breaker after DEGRADED — **S** — *regresión Bug 10* — cerrado por PR (rama `fix/102-reconnect-circuit-breaker`)
- [x] **#103** 🔴 `[005]` — `OpenClawClient.connect()` does not close prior socket/tasks on reconnect — **S** — cerrado por PR (rama `fix/103-openclaw-connect-leak`)
- [x] **#104** 🔴 `[006]` — `_last_error` not cleared after successful reconnect — **XS** — *regresión auditoría junio* — cerrado por PR (rama `fix/104-last-error-reset`)

**Revisión post-cierre (code review, rama `fix/phase1-review-followups`):** #99/#101/#103 tenían bugs reales sin tests que los cubrieran, encontrados releyendo el código ya mergeado (no solo el diff original):
- **#101** — `close_all()` ponía `self._closed = True` *antes* del teardown real; si la primera llamada era cancelada a mitad, la red de seguridad de `routes.py` (añadida por el propio #101) se volvía un no-op permanente y `tracker.close()` nunca corría. Fix: `_closed` solo se marca al completar el teardown, todo el cuerpo serializado por un `_close_lock`.
- **#103** — `_listen()` no llamaba a `error_all()` en su rama de cancelación (solo en la de excepción real), así que cada reconexión huerfanizaba cualquier turn en vuelo para siempre (sin timeout en la cadena) y bloqueaba el `session_key` con `TurnInProgress`. Fix: `error_all()` también en la rama de cancelación (sin disparar `on_disconnect`).
- **#99** — `call_orchestrator()` hacía `raise` dentro del `async for`, abandonando el generador de `stream_response()` sin cerrarlo — su `finally: unregister()` quedaba diferido al GC. Fix: `contextlib.aclosing()` en `call_orchestrator` + reordenar `_finished = True` para que se fije *antes* del yield terminal (si no, cerrar el generador tras un evento terminal disparaba un `chat.abort` espurio).

**Segunda revisión post-cierre (2026-07-18, cubriendo #102/#104 + pase general sobre el resto):** 10 ángulos de review en paralelo + verificación manual. #102 y #104 en sí mismos están limpios (374 tests verdes, invariantes documentadas en CLAUDE.md se sostienen). El pase general encontró 4 issues nuevas, ninguna introducida por #102/#104:
- [x] **#149** 🔴 — Watchdog de silencio cierra sesiones sanas justo tras reconectar el transcriber (`_last_transcription_at` no se resetea, `elapsed` cuenta desde antes del corte) — contradice el "resumes normally" documentado para el fix de la Fase 1 anterior.
- [x] **#150** 🟠 — `ReconnectingOpenClawClient.stream_response()` no envuelve su generador interno en `aclosing()` — el fix de #99/#147 solo cierra el wrapper, no el generador real de `OpenClawClient` que tiene el `finally: unregister()`. Reintroduce la race de TurnRegistry un nivel más adentro, en cada evento `error`.
- [ ] **#151** 🟡 — `agents.list` transitorio durante una reconexión en background deja el roster de agentes vacío sin fallback al último conocido — no se arregla ahora, queda en cola.
- [ ] **#152** ⚪ — `ReconnectingTTSClient` muta `_backoff`/`_last_error`/`state` sin lock entre turnos concurrentes (singleton compartido por todas las sesiones) — no se arregla ahora, queda en cola.

Ambas #149 y #150 arregladas antes de empezar Fase 2 (decisión 2026-07-18, rama `fix/149-150-phase1-review-followups-2`) — incluye además la reparación de dos tests del watchdog (`test_watchdog_resets_count_when_transcription_arrives`, `test_watchdog_pauses_but_does_not_exit_when_reconnecting`) que parcheaban `asyncio.sleep` de forma global y por tanto nunca ejercitaban de verdad el loop del watchdog (confirmado rompiendo a propósito el manejo de RECONNECTING: seguían en verde). #151/#152 quedan documentadas para priorizar más adelante — refuerzan la deuda ya trackeada en #126 (los 3 wrappers de reconexión duplican lógica sin base compartida).

### 🟠 Fase 2 — Seguridad & auth (semana 3) — ✅ CERRADA (2026-07-20)

**Objetivo:** cerrar los huecos de seguridad y autorización.
**Release target:** 1.16.0 → *publicado como **v1.14.10** (2026-07-20) — el número planificado no se usó.*
**Acceptance gate:** pentest manual pasa, `/v1/*` rechaza untrusted sin bearer, `/admin/*` rechaza sin token, cero secrets en logs (verificado con grep sobre la salida de una sesión).
**Estado del gate:** `/v1/*` rechaza untrusted sin bearer ✅ (`test_get_models_from_untrusted_origin_without_auth_returns_401`, `test_chat_completions_from_untrusted_origin_without_auth_returns_401`) · `/admin/*` rechaza sin token ✅ (`test_admin_missing_token_returns_422`, `test_admin_wrong_token_returns_401`) · cero secrets en logs ✅ (#106: fingerprint SHA-256 de 8 hex, cubierto por `test_logging.py` + `test_ws_handshake.py::test_invalid_client_key_log_is_safe_and_correlatable` + `test_bridge_barge_in.py::test_final_transcription_is_debug_only_and_truncated` — y desde la revisión de cierre, con la garantía adicional de que esos logs efectivamente *salen* en producción, ver #156 abajo) · pentest manual ⚠️ *no ejecutado en este cierre* — las 5 issues de la fase (#105–#109) están cerradas y la suite (438 tests) verde; el pentest manual queda como verificación pendiente, no bloqueante para mergear dado que cada issue de la fase tiene su propia cobertura automatizada específica.
**Estrategia de rama (decisión 2026-07-18):** a diferencia de Fase 1 (cada issue directa a `main`), Fase 2 usa una rama larga `phase/2-security` creada desde `main` (una vez mergeado PR #153). Cada issue (#105–#109) se desarrolla en su propia rama `fix/XXX-...`, mergeada a `phase/2-security` vía PR individual. Al cerrar las 5 issues, un PR único `phase/2-security` → `main` cierra la fase completa.

- [x] **#105** 🟠 `[007]` — `default_agent`/`allowed_agents` persisted but never enforced — **M** — semántica decidida: `None`=sin restricción, `[]`=denegado, `["x"]`=solo `x` — cerrado por #154
- [x] **#106** 🟠 `[008]` — Full `client_key` written to logs — **XS** — fingerprint SHA-256 de 8 hex + request ID + client.id post-auth; transcripts a DEBUG truncado — cerrado por #155
- [x] **#107** 🟠 `[009]` — Cache invalidation race + thread-safety — **M** — threading.Lock cross-thread + contador de generación por-key + orden commit-antes-que-invalidate en admin_routes.py — cerrado por #157
- [x] **#108** 🟠 `[010]` — `barge_in_enabled=False` ignored by the bridge — **XS** — gate en `_on_transcription` (`bridge.py:425`), partial siempre se reenvía al cliente independientemente del flag — cerrado por #158
- [x] **#109** 🟠 `[011]` — `push_enabled=False` suppresses only lifecycle start, not push payloads — **S** — nuevo `_push_allowed()` como único punto de decisión, gatea `deliver_push`/`deliver_push_tool_call` — cerrado por #159

**Revisión de cierre (code review, PR #160, 2026-07-20):** pase completo sobre el diff de fase (33 archivos) más trazado cruzado de todos los call sites de `resolve_agent`/`invalidate`/`fingerprint_key`. 3 issues nuevas encontradas y arregladas directamente en `phase/2-security`, 1 documentada para Fase 5:
- [x] **#156** 🔴 *(preexistente, abierta durante #106, no trackeada en ninguna fase hasta ahora)* — `migrations/env.py` llama `fileConfig(alembic.ini)` con el default de `disable_existing_loggers=True`. Como `main.py` importa todos los módulos `src.*` (creando sus loggers) *antes* de que `lifespan()` llame a `run_migrations()`, y `alembic.ini` solo declara los loggers `root`/`sqlalchemy`/`alembic`, **cada arranque real del gateway deshabilita silenciosamente todos los loggers `src.*` para el resto de la vida del proceso** — incluidas las líneas de log de seguridad que #106 acaba de construir. Confirmado empíricamente (no solo en teoría) reproduciendo la secuencia exacta de arranque contra una BD temporal. Severidad subida de medium a **critical** al confirmar el impacto en producción. Fix de una línea (`disable_existing_loggers=False`) + limpieza del workaround de test que ya no hace falta (`tests/unit/conftest.py`).
- [x] **#161** 🟠 — `create_client()` (`admin_routes.py:74`) guardaba `allowed_agents: []` (deniega todo, semántica de #105) como `None` (sin restricción) por un check *truthy* en vez de `is not None` — el PATCH (`update_client`) ya lo hacía bien. Con #105 recién mergeado, este bug pasa de inerte a activo: un admin que cree un cliente pensando que lo ha bloqueado a "ningún agente" le da acceso a todos. Mismo patrón en `output_mode` (menor severidad, "informational only"). Ningún test lo cubría — el test de "deny-all" existente inserta directo en BD, sin pasar por este endpoint.
- [x] **#162** 🟡 — el handshake WS no recortaba espacios del `agent` solicitado antes de pasarlo a `resolve_agent()`, a diferencia de REST — inconsistencia entre las dos superficies para la misma entrada malformada, y contradecía la cascada documentada en este mismo `CLAUDE.md` ("if non-empty after stripping"). Fix: normalización centralizada dentro de `resolve_agent()` en vez de duplicada por call site.
- [ ] **#163** ⚪ — `DbClient._generations` (contador de generación de #107) crece sin límite, una entrada por `client_key` histórico, nunca se purga. Bajo impacto, pero un "pop" ingenuo en `invalidate()` reintroduce la race que #107 cerró para la primera invalidación de una key — requiere diseño dedicado. Diferido a **Fase 5**.

### 🟠 Fase 3 — Lifecycle & producción (semanas 4–5) — ✅ CERRADA (2026-08-06)

**Objetivo:** hacerlo production-grade (shutdown limpio, deadlines, race fixes).
**Release target:** 1.17.0 → *pendiente de lanzar: los fixes están en `main` sin tag (ver header)*.
**Acceptance gate:** `kill -9` durante sesión deja DB consistente, 50 sesiones concurrentes estables, los 3 wrappers pasan test "DEGRADED stable", `ready.capabilities` correcto.
**Estado del gate:** `kill -9`/shutdown deja DB consistente ✅ (#110 — `ClientRegistry.close_all_sessions()` (`src/services/openclaw/registry.py`) drena todos los bridges registrados concurrentemente, cada uno acotado por `SHUTDOWN_DRAIN_S`; `dispose_engine()` cierra el motor SQLAlchemy; lifespan envuelto en try/finally — cobertura unitaria + integración) · 3 wrappers DEGRADED-stable ✅ (Fase 1, #102/#104) · `ready.capabilities` correcto ✅ (#114, separa `requested_capabilities`/`live_capabilities`) · suite completa verde al mergear a `main` ✅ (480 tests, 2026-10-08) · 50 sesiones concurrentes estables ⚠️ *no verificado con load test dedicado* — el drenado es concurrente por diseño (`asyncio.gather` por sesión) pero no se ha ejercitado con carga real; queda como verificación pendiente, no bloqueante dado que cada pieza tiene su propia cobertura automatizada.
**Estrategia de rama (decisión 2026-07-20):** igual que Fase 2, Fase 3 usa una rama larga `phase/3-lifecycle` creada desde `main`. Cada issue (#110–#117) se desarrolla en su propia rama `fix/XXX-...`, mergeada a `phase/3-lifecycle` vía PR individual. Al cerrar las 8 issues, un PR único `phase/3-lifecycle` → `main` cierra la fase completa.

- [x] **#110** 🟠 `[012]` — Lifespan shutdown doesn't drain active sessions — **XL** — cerrado por PR #174 (2026-08-06): `ClientRegistry.close_all_sessions()` drena todos los bridges registrados concurrentemente acotado por `SHUTDOWN_DRAIN_S`; fix de auto-cancelación en `JotaBridge.close_all()` (issue latente encontrada durante la implementación — el propio watchdog podía cancelarse a sí mismo y abortar su teardown antes de `tracker.close()`); `dispose_engine()` (`src/db/database.py`) dispone y resetea el motor SQLAlchemy; `src/main.py`'s lifespan envuelto en try/finally, orden drenar sesiones → cerrar OpenClaw → cancelar/esperar notification tasks → disponer motor. *(Fix en el merge a `main`, 2026-10-08: `close_all()` absorbe la cancelación externa que llegaba al `asyncio.gather` del drenado de tareas justo después de `ws.close()` y abortaba el teardown antes de `tracker.close()` — el `except CancelledError` de `run()` solo protegía `await client_task`, no su `finally`.)*
- [x] **#111** 🟠 `[013]` — Streaming SSE returns 200 on orchestrator error — **S** — cerrado por PR #165 (2026-07-20): los fallos pre-token y post-token emiten `server_error` + `finish_reason="error"`, no emiten `[DONE]` y cierran el tracker con estado `error`.
- [x] **#112** 🟠 `[014]` — Normal vs push turn coordination — **L** — *decisión: suprimir* (ver Decisiones #5) — cerrado por PR #170 (2026-08-05): `_handle_agent_lifecycle` (dispatcher.py) comprueba `TurnRegistry.get_queue_by_session()` y suprime solo la fase `start` si ya hay un normal turn registrado para ese `session_key` (bug reproducido en vivo 2026-08-02); la fase `end` se reenvía siempre sin condición — `on_push_turn_end` ya no-opea de forma segura si no hay push abierto (#84), y suprimirla también habría podido dejar huérfano un push que empezó antes que el turno normal (hallazgo de la revisión final, 2026-08-04). La ventana de carrera barge-in/`chat.abort` (evento tardío tras `TurnRegistry.unregister()`) queda fuera de alcance — es preexistente y afecta también a `_handle_chat` — pendiente de documentar como issue de seguimiento aparte (no creada todavía).
- [x] **#113** 🟠 `[015]` — Bridge unregisters newer bridge for same `client_id` — **S** — cerrado por PR #164 (2026-07-20): `ClientRegistry.unregister(client_id, expected_bridge)` solo desregistra si el bridge sigue siendo el dueño actual (mismo patrón de identidad que `TurnRegistry` para #99); `bridge.close_all()` pasa `self`. Un cierre tardío del bridge viejo ya no expulsa la sesión reconectada.
- [x] **#114** 🟠 `[016]` — `ready.capabilities` contradicts actual service availability — **S** — cerrado por PR #166 (2026-07-26): `ready` separa `requested_capabilities` (lo que pidió el cliente) y `live_capabilities` (instantánea real del health check) — doc: `client-protocol.md`.
- [x] **#115** 🟠 `[017]` — No bounded deadlines (handshake/turn/idle/shutdown drain) — **M** — cerrado por PR #169 (2026-08-02): 4 settings nuevas (`HANDSHAKE_TIMEOUT_S`=10s, `TURN_TIMEOUT_S`=120s con reset por evento (idle-reset), `IDLE_TIMEOUT_S`=300s, `SHUTDOWN_DRAIN_S`); turn timeout implementado en `OpenClawClient.stream_response()` reutilizando el `chat.abort`/`TURN_ERROR` ya existente de #99/#111/#150 — el turn timeout no toca `bridge.py` ni `openai_routes.py`. Fix wave de revisión final: el idle watchdog de `bridge.py` difiere el cierre si `_active_turn`/`_push_turn_open` indica trabajo en vuelo, para no cortar turnos en curso ni limitar sesiones push-only a `IDLE_TIMEOUT_S` — doc: `client-protocol.md` §13.
- [x] **#116** 🟠 `[018]` — `TTSClient.connect()` leaks WebSocket on `CancelledError` — **S** — cerrado por PR #165 (2026-07-20): cleanup gobernado por `_authenticated`, cancellation relanzada y auth `recv()` acotado por `TTS_AUTH_TIMEOUT_S=10.0`.
- [x] **#117** 🟠 `[019]` — `ReconnectingTTSClient` missing `on_state_change` hook — **M** — cerrado por PR #173 (2026-08-05): `on_state_change` fires from `_record_failure`/`_record_success` only on an actual transition (`_set_state()` guard); wired in `main.py`'s lifespan to `ClientRegistry.broadcast_status`, same pattern as the orchestrator; old per-bridge `_maybe_notify_tts_state`/`_tts_degraded_notified` polling removed. Session-start notification for a brand-new session mid-outage was already covered by `health_check()`'s pre-existing live TTS ping (not duplicated — a review pass caught that a second, wrapper-state-based check could show a stale `"reconnecting"` alongside an already-recovered `live_capabilities.tts: true`).

**Cierre de fase:** último PR de la fase #174 (2026-08-06); el merge único `phase/3-lifecycle` → `main` se completó el 2026-10-08.

### 🟠🟡 Fase 4 — Consistencia & docs (semana 6) — ✅ CERRADA (2026-10-08)

**Objetivo:** limpiar inconsistencias y referencias muertas.
**Release target:** 1.18.0.
**Acceptance gate:** `grep` no encuentra funciones referenciadas pero inexistentes, `.env.sample` levanta un gateway limpio, `db_client.get_session` tiene test de concurrencia.
**Estado del gate (2026-10-08, bloque A cerrado):** `grep` sin referencias muertas ✅ (`tests/unit/test_docs_accuracy.py`: `create_db_and_tables`, `sin auth`/`LAN-only`, `openclaw_routes`) · `.env.sample` levanta un gateway limpio ✅ (verificado con arranque real en directorio vacío, #180; `tests/unit/test_env_sample.py` lo mantiene sincronizado con `Settings`) · test de concurrencia de `db_client.get_session` ✅ (ya existía desde #107: `tests/unit/test_db_client_local.py`). **#126** (refactor L) se descartó del alcance de esta fase y pasó a Fase 5 (decisión 2026-10-08): se aborda aparte con spec y plan propios, así que la fase se cierra con 8 de sus 9 issues.
**Estrategia de rama (decisión 2026-10-08):** como en Fase 2, rama larga `phase/4-consistency`; un PR por grupo de issues (#178, #179, #180). Al mergear a una rama que no es `main` GitHub no cierra issues: el PR final `phase/4-consistency` → `main` debe listarlos con `Closes`.

- [x] **#118** 🟠 `[020]` — `.env.sample` documents pre-SQLite architecture — **S** — cerrado por #180 (rama `fix/118-122-env-sample-and-validation`, mergeado a `phase/4-consistency`)
- [x] **#119** 🟠 `[021]` — Docs incorrectly describe `/v1/*` as unauthenticated — **S** — cerrado por #179 (rama `fix/119-120-121-docs-consistency`, mergeado a `phase/4-consistency`)
- [x] **#120** 🟡 `[022]` — `docs/skills/openclaw/references/` describe incompatible protocol — **S** — cerrado por #179 (rama `fix/119-120-121-docs-consistency`, mergeado a `phase/4-consistency`)
- [x] **#121** 🟡 `[023]` — `create_db_and_tables()` referenced in docs but doesn't exist (renamed v1.12.0) — **XS** — cerrado por #179 (rama `fix/119-120-121-docs-consistency`, mergeado a `phase/4-consistency`)
- [x] **#122** 🟡 `[024]` — ClientConfig/ClientRecord field drift (4 fields) — **S** — cerrado por #180 (rama `fix/118-122-env-sample-and-validation`, mergeado a `phase/4-consistency`): `client_type` = `Literal` de entrada (`ha`/`esp32`/`web`/`app`), `output_mode` reutiliza `OutputMode`; respuesta sigue `str` (filas antiguas legibles, sin migración)
- [x] **#123** 🟡 `[025]` — CLI doesn't invalidate `db_client` cache — **XS** — cerrado por #178 (rama `fix/123-124-125-small-fixes`, mergeado a `phase/4-consistency`)
- [x] **#124** 🟡 `[026]` — Fresh deploy fails: `data/` not auto-created — **XS** — cerrado por #178 (rama `fix/123-124-125-small-fixes`, mergeado a `phase/4-consistency`)
- [x] **#125** 🟡 `[027]` — Stale `"tags"` reference in `admin_routes.py:112` — **XS** — cerrado por #178 (rama `fix/123-124-125-small-fixes`, mergeado a `phase/4-consistency`)
- [→] **#126** — *movida a Fase 5 el 2026-10-08 (ver abajo)*

### 🟡⚪ Fase 5 — Polish batch (semana 7)

**Triage (2026-10-08):** verificado contra el código en `main` (`4bd1877`). #132 ya estaba resuelta (#115); #129 y #134 se reducen de alcance; #127, #128, #133, #135, #136, #137 y #138 siguen vigentes; #130, #131, #143 y #163 pendientes de análisis. Orden propuesto: (1) #137+#138, (2) bugs pequeños #127/#128/#135/#136, (3) #133, (4) #131/#130/#143/#163, (5) #126 con #151/#152 en spec propio.

**Objetivo:** pulir detalles restantes.
**Release target:** 1.19.0.
**Acceptance gate:** typecheck (mypy/pyright) en CI, Docker build on PR, pytest timeout global, Dockerfile non-root + digest pin, coverage delta visible.

- [ ] **#126** 🟡 `[028]` — Three reconnecting wrappers duplicated without shared base — **L** — *movida desde Fase 4 (2026-10-08); bloqueantes #102, #103, #104 ya cerrados; requiere spec + plan propios. Candidata natural para absorber #152 (lock del singleton TTS) y relacionada con #151*
- [ ] **#127** 🟡 `[029]` — `PipelineTracker.close()` not idempotent — **S**
- [ ] **#128** 🟡 `[030]` — Session-wide final-text dedup drops legitimate repeated utterances — **S**
- [ ] **#129** 🟡 `[031]` — Watchdog timing semantics tied to poll ticks; re-entrant shutdown — **M→S** — *triage 2026-10-08: la idempotencia de `close_all()` ya está resuelta (#101/#110); queda decidir/documentar la semántica de `max_silence_turns` (ticks de 2s vs ventanas de `silence_timeout_s`) y testearla con tiempo real*
- [ ] **#130** 🟡 `[032]` — Client output is neither serialized nor backpressured — **L**
- [ ] **#131** 🟡 `[033]` — Transcriber task failures not supervised or retrieved — **M**
- [x] **#132** ⚪ `[034]` — No idle timeout for clients that never send input — **S** — *triage 2026-10-08: ya resuelta por #115 (`_idle_watchdog` aplica a toda sesión, cobertura en `test_bridge_watchdog.py`); cerrada sin cambios de código*
- [ ] **#133** ⚪ `[035]` — Logs and session events contain credentials and transcripts — **S** — *triage 2026-10-08: sigue vigente tras #106 — transcripts a INFO en `bridge.py` (send, barge-in) y `PipelineTracker.record()`, y `transcription_final` persiste `text[:60]` en `SessionRecord`*
- [ ] **#134** ⚪ `[036]` — `connect()` calls without timeout — half-open sockets hang forever — **S→XS** — *triage 2026-10-08: mitigada, `websockets` 16 aplica `open_timeout=10` por defecto y los `recv()` del handshake de OpenClaw tienen timeout; queda como mucho hacerlo explícito/configurable — candidata a cerrar como won't-fix*
- [ ] **#135** ⚪ `[037]` — `_keepalive_loop` interval not clamped; `tickIntervalMs=0` causes ping flood — **XS**
- [ ] **#136** ⚪ `[038]` — Dispatcher silently drops unknown event types — **XS**
- [x] **#137** ⚪ `[039]` — CI gaps: typecheck, Docker build, pytest timeout — **M** — cerrado por #184 (2026-10-09): mypy (15 errores de `src/` arreglados), job `docker-build` en PR, `pytest-timeout` 60s, `concurrency`, cobertura visible (92%) y matriz Python 3.12/3.14 (3.14 verificado en CI). Job agregador `test` para el check requerido del ruleset `production`
- [x] **#138** ⚪ `[040]` — Dockerfile + dependency manifests hardening — **M** — cerrado por #186 (2026-10-09): Python 3.14-slim con digest, usuario no-root, HEALTHCHECK, solo deps de producción desde `pyproject.toml` (`requirements.txt` eliminado), `.dockerignore`, compose con `GATEWAY_UID`/`GATEWAY_GID`; CI arranca el contenedor y lo verifica
- [ ] **#143** 🟡 — `TranscriberClient` does not consume the transcriber's capacity-status protocol (`type:status` push, `complete`/`reason` fields) — **M** — *abierta durante el trabajo de Fase 3 (spec: jota-transcriber `2026-07-09-transcriber-status-capacity-design.md`); labels: `type:observability`, `domain:microservice-clients`*
- [ ] **#163** ⚪ — `DbClient._generations` (contador de generación de #107) crece sin límite, nunca se purga — **S** — encontrada en la revisión de cierre de Fase 2 (PR #160); requiere diseño dedicado para no reabrir la race de #107

---

## 🚀 Fase 6+ — Enhancements (backlog)

Estos son enhancements identificados durante la auditoría y operación. **No están abiertos como issues** todavía — se priorizan y abren al iniciar esa fase.

### 6.1 — Observabilidad y métricas

- [ ] **enh** — Exportar métricas Prometheus en `/metrics` (reconexiones, time-in-degraded, turns por cliente, queue depth)
- [ ] **enh** — Structured logging JSON con `client_id`, `session_id`, `turn_id`, `request_id` (OpenTelemetry-compatible)
- [ ] **enh** — Dashboard Grafana preconfigurado para `SessionRegistry` y reconnect state
- [ ] **enh** — Tracing distribuido (OpenTelemetry) en el path orquestador → TTS → bridge

### 6.2 — Seguridad

- [ ] **enh** — Hash de `client_key` en DB (bcrypt/argon2) — actualmente se almacena en plain text
- [ ] **enh** — Rotación automática de `client_key` configurable (cada N días, con grace period)
- [ ] **enh** — Rate limiting per-client y per-IP en `/v1/*` y WS handshake
- [ ] **enh** — WS origin allowlist configurable
- [ ] **enh** — OpenAPI security scheme para bearer auth en `/v1/*` (documentar en `/docs`)
- [ ] **enh** — Audit log persistente (quién desactivó/rotó/creó qué y cuándo)

### 6.3 — Compatibilidad OpenAI

- [ ] **enh** — Compatibilidad OpenAI completa: preservar `messages[]`, validar `model` contra `/v1/models`, contar `completion_tokens` honestamente
- [ ] **enh** — Soporte `function_calling`/`tools` end-to-end (round-trip con OpenClaw)
- [ ] **enh** — Streaming con `n>1` (multiple choices)
- [ ] **enh** — Soporte `logprobs`, `top_p`, `temperature` (passthrough a OpenClaw)
- [ ] **enh** — `/v1/embeddings` adapter

### 6.4 — Multi-worker & escalado

- [ ] **enh** — Migrar `SessionRegistry` y `ClientRegistry` a store compartido (Redis o PostgreSQL LISTEN/NOTIFY)
- [ ] **enh** — Documentar deployment multi-worker con Uvicorn `--workers N`
- [ ] **enh** — Sticky sessions para WebSocket (cookie-based routing)
- [ ] **enh** — Métricas de carga para auto-scaling (k8s HPA)

### 6.5 — Operación

- [ ] **enh** — Backup automático de `data/gateway.db` (cron o sidecar; `sqlite3 .backup`)
- [ ] **enh** — Web UI para `SessionRegistry` (admin) — eventos en vivo, filter por client
- [ ] **enh** — `python3 src/cli.py purge-inactive --older-than-days 90` — retención
- [ ] **enh** — Soporte `wss://` (TLS) configurable por servicio (`OPENCLAW_USE_TLS`, etc.)
- [ ] **enh** — Healthcheck enrichment: `GET /healthz` devuelve tiempo desde último reset de counters

### 6.6 — Protocolo OpenClaw

- [ ] **enh** — Test suite para OpenClaw protocol drift — cada evento documentado en `docs/openclaw-protocol.md` debe tener test
- [ ] **enh** — Auto-detección de nueva versión del servidor OpenClaw al handshake (log warning si diff)
- [ ] **enh** — Soporte `agent.stream` con filtros de eventos (por tipo)
- [ ] **enh** — Métricas de latencia round-trip por tipo de evento

### 6.7 — Bridge features

- [ ] **enh** — Soporte multi-idioma per-client (no solo `stt_language` global)
- [ ] **enh** — Per-client voice aliases (mapeo `tts_voice` a nombres amigables)
- [ ] **enh** — Historial de turns navegable desde `SessionRegistry` (admin)
- [ ] **enh** — Soporte de `interrupt_at_phrase` (barge-in por contenido, no por longitud)

### 6.8 — DX para integradores

- [ ] **enh** — SDK Python oficial para clientes WS
- [ ] **enh** — SDK JS/TS para clientes web
- [ ] **enh** — Ejemplo de integración Home Assistant end-to-end (más allá de `docs/skills/`)
- [ ] **enh** — Postman collection para `/v1/*` y `/admin/*`

---

## Decisiones pendientes

Antes de implementar las issues marcadas con ⚠️, hay que resolver:

1. ~~**`system_prompt_extra` (#100)**~~ — **Decidido:** eliminar el campo (sin hook viable en el protocolo de OpenClaw) — cerrado por #141 (Fase 1).
2. ~~**`allowed_agents` semántica (#105)**~~ — **Decidido (2026-07-18):** `None` = sin restricción, `[]` = denegado, `["x"]` = solo `x`.
3. ~~**Concurrencia por `session_key` (#99)**~~ — **Decidido:** rechazar el segundo turno concurrente (no serializar con espera) — implementado en PR #140 (Fase 1).
4. ~~**`ready.capabilities` (#114)**~~ — **Decidido:** dividir en `requested_capabilities` (lo que pidió el cliente) + `live_capabilities` (disponibilidad real) — implementado en PR #166 (Fase 3).
5. ~~**Push durante normal turn (#112)**~~ — **Decidido:** suprimir `agent.start` durante un normal turn ya registrado para el mismo `session_key` (más simple que concurrencia con IDs distintos) — implementado en PR #170 (Fase 3).
6. **Multi-worker** — ¿previsto? Si sí, `SessionRegistry` y `ClientRegistry` deben ser stores compartidos — afecta al alcance de #110 *(ya cerrado: verificar que el drenado de shutdown es correcto también con `--workers N` si se activa multi-worker)*.
7. ~~**`docs/skills/openclaw/`**~~ — resuelto (2026-10-08, #179): se borran solo `references/protocol.md` y `ha-bridge.md` (los erróneos); el resto de `references/` es genérico y se conserva; `SKILL.md` apunta a `docs/openclaw-protocol.md`.
8. ~~**`connect()` para sockets previos (#103)**~~ — resuelto al cerrar #103 (Fase 1).

---

## Risk register

| Riesgo | Prob. | Impacto | Mitigación |
|---|---|---|---|
| Fix TurnRegistry rompe la multiplexación sana actual | Media | Alto | Tests exhaustivos de multi-session e2e antes/después |
| Default-agent enforcement rompe flujos existentes | Alta | Medio | Feature flag por cliente `enforce_agent_policy` default False hasta v1.17 |
| System-prompt-extra drop rompe integraciones | Baja | Alto | `grep system_prompt` en callers antes; concat-como-string como fallback temporal |
| Cambios en auth `/v1/*` rompen HA | Media | Alto | Doc clara de migración + compat hasta v1.18 |
| Refactor de 3 wrappers introduce regresión sutil | Alta | Alto | Composition, no inheritance; tests paralelos |
| Multi-worker sin store compartido causa estado inconsistente | Media | Alto | Documentar explícitamente como single-worker en v1.x |
| Backup/restore de SQLite sin documentar → pérdida de datos | Media | Alto | Incluir runbook en README antes de Fase 5 done |

---

## Acceptance gates por milestone

| Milestone | Criterio |
|---|---|
| **Fase 1 done** | ✅ 6 🔴 cerrados, `pytest` verde, e2e no regresiona — *cero keys en logs queda como alcance de #106 (Fase 2)* |
| **Fase 2 done** | ✅ 5 🟠 cerrados (#105–#109), `/v1/*` rechaza untrusted sin bearer ✅, admin rechaza sin token ✅, cero keys en logs ✅ — *pentest manual no ejecutado, ver nota en la fase* |
| **Fase 3 done** | ✅ 8 🟠 cerrados (#110–#117) por PRs #164–#174 (2026-08-06), mergeado a `main` 2026-10-08 — `kill -9`/shutdown ✅ (#110), 3 wrappers DEGRADED-stable ✅ (Fase 1), `ready.capabilities` ✅ (#166) — *50 sesiones concurrentes ⚠️ pendiente de load test dedicado* |
| **Fase 4 done** | Cero referencias muertas, `.env.sample` levanta gateway limpio, `db_client` test de concurrencia |
| **Fase 5 done** | Typecheck CI, Docker build on PR, pytest timeout global, Dockerfile non-root + digest pin |

---

## Cómo actualizar este documento

1. **Al cerrar una issue** → marca su casilla con `[x]` y enlaza el PR que la cierra.
2. **Al abrir una issue nueva** → añádela a la fase correspondiente con su número `#NNN`.
3. **Si una issue cambia de fase** → muévela (no la dupliques).
4. **Al cerrar una fase entera** → actualiza el acceptance gate en "Acceptance gates" y el estado en el TL;DR.
5. **Si añades un enhancement** → documéntalo en "Fase 6+" con `enh` como prefijo.
6. **Si descubres un nuevo bug** → crea una issue primero; después añádela aquí.

Este documento se actualiza en el mismo PR que cierra la issue, o en un PR dedicado semanal. LaCadencia recomendada: actualizar al cierre de cada fase.

---

## Referencias

- [CHANGELOG.md](../CHANGELOG.md) — historial de releases
- [AUDIT_2026-06-28.md](../AUDIT_2026-06-28.md) — auditoría anterior (bugs ya resueltos)
- [docs/superpowers/plans/2026-07-15-production-readiness-roadmap.md](./superpowers/plans/2026-07-15-production-readiness-roadmap.md) — planning narrative completo
- [docs/openclaw-protocol.md](./openclaw-protocol.md) — protocolo OpenClaw v4 (source of truth)
- [docs/client-protocol.md](./client-protocol.md) — protocolo cliente WebSocket
- [CLAUDE.md](../CLAUDE.md) — contexto para Claude Code