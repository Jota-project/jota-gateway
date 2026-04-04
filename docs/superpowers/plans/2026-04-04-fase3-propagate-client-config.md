# Fase 3 — Propagar ClientConfig a TTS, Orchestrator y Barge-in

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hacer que TTS, Orchestrator y el umbral de barge-in usen los valores de `ClientConfig` del cliente en lugar de los globales de `settings`.

**Architecture:** Toca dos repos. En `jota-orchestrator`: `QuickRequest` acepta `system_prompt_extra` y lo añade al system prompt antes de inferir. En `jota-gateway`: `TTSClient.connect()` acepta `voice`/`speed`, `OrchestratorClient.stream_response()` acepta `system_prompt_extra`, y `JotaBridge` propaga todos esos valores desde `self.config`. El barge-in pasa de usar `settings.BARGE_IN_MIN_CHARS` a `self.config.barge_in_min_chars`.

**Tech Stack:** Python 3.11, pytest + pytest-asyncio (asyncio_mode=auto), unittest.mock

---

## File Map

| Repo | Archivo | Cambio |
|---|---|---|
| jota-orchestrator | `src/api/quick.py` | `QuickRequest` añade `system_prompt_extra: Optional[str]`; `_quick_stream_generator` lo anexa al `full_prompt` |
| jota-gateway | `src/services/tts_client.py` | `connect()` acepta `voice` y `speed` opcionales → los incluye en el mensaje `auth` |
| jota-gateway | `src/services/orchestrator_client.py` | `stream_response()` y `listen_loop()` aceptan `system_prompt_extra` → lo incluye en el payload si no es `None` |
| jota-gateway | `src/services/bridge.py` | `_call_orchestrator()` pasa `voice`, `speed`, `model_id`, `system_prompt_extra` desde `self.config`; `_on_transcription()` usa `self.config.barge_in_min_chars` |
| jota-orchestrator | `tests/unit/test_quick_system_prompt.py` | NUEVO — tests del system prompt con/sin `system_prompt_extra` |
| jota-gateway | `tests/unit/test_tts_connect.py` | NUEVO — tests de `TTSClient.connect()` con voice/speed |
| `tests/unit/test_orchestrator_system_prompt.py` | NUEVO — tests de payload con `system_prompt_extra` |
| `tests/unit/test_bridge_barge_in.py` | Añadir test que verifica que `config.barge_in_min_chars` se usa en lugar del global |
| `tests/unit/test_bridge_config_propagation.py` | NUEVO — tests de que el bridge pasa la config correcta a TTS y orchestrator |

---

## Task 0: jota-orchestrator — QuickRequest acepta system_prompt_extra

**Repo:** `/home/sito/jota-orchestrator`

**Files:**
- Modify: `src/api/quick.py:43-46` (QuickRequest) y `:49-54` + `:61-63` (_quick_stream_generator)
- Create: `tests/unit/test_quick_system_prompt.py`

El objetivo es que cuando el gateway envíe `system_prompt_extra` en el payload, el orchestrator lo anexe al system prompt antes de inferir. El campo es opcional — si es `None` o vacío, el comportamiento actual no cambia.

- [ ] **Step 1: Escribir los tests que fallarán**

Crear el fichero (comprobar primero si existe directorio `tests/unit/` en jota-orchestrator):

```python
# /home/sito/jota-orchestrator/tests/unit/test_quick_system_prompt.py
"""Tests: _quick_stream_generator appends system_prompt_extra to full_prompt."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


async def _collect(gen) -> list[dict]:
    results = []
    async for line in gen:
        line = line.strip()
        if line:
            results.append(json.loads(line))
    return results


@pytest.fixture
def mock_inference():
    """Patch inference_client so it yields a single token and creates/closes sessions."""
    with patch("src.api.quick.inference_client") as mock:
        mock.create_session = AsyncMock(return_value="sess-1")
        mock.close_session = AsyncMock()
        mock.set_context = AsyncMock()

        async def fake_infer(*args, **kwargs):
            yield "hola"

        mock.infer = MagicMock(side_effect=fake_infer)
        yield mock


@pytest.fixture
def mock_tools():
    with patch("src.api.quick.tool_manager") as mock:
        mock.get_system_prompt_addition = MagicMock(return_value=None)
        yield mock


async def test_system_prompt_extra_appended_when_provided(mock_inference, mock_tools):
    """system_prompt_extra is appended to the full_prompt passed to infer()."""
    from src.api.quick import _quick_stream_generator

    events = await _collect(
        _quick_stream_generator(
            client_id="cid",
            session_id="sess-1",
            text="hola",
            model_id=None,
            system_prompt_extra="responde siempre en inglés",
        )
    )

    call_kwargs = mock_inference.infer.call_args.kwargs
    assert "responde siempre en inglés" in call_kwargs["params"]["system_prompt"]


async def test_system_prompt_extra_not_present_when_none(mock_inference, mock_tools):
    """When system_prompt_extra is None, the base prompt is unchanged."""
    from src.api.quick import _quick_stream_generator
    from src.api.quick import QUICK_SYSTEM_PROMPT

    events = await _collect(
        _quick_stream_generator(
            client_id="cid",
            session_id="sess-1",
            text="hola",
            model_id=None,
            system_prompt_extra=None,
        )
    )

    call_kwargs = mock_inference.infer.call_args.kwargs
    system_prompt = call_kwargs["params"]["system_prompt"]
    # No extra text beyond the base + optional tool instructions
    assert system_prompt.strip().startswith(QUICK_SYSTEM_PROMPT.strip())


async def test_system_prompt_extra_empty_string_ignored(mock_inference, mock_tools):
    """Empty string is not appended."""
    from src.api.quick import _quick_stream_generator
    from src.api.quick import QUICK_SYSTEM_PROMPT

    events = await _collect(
        _quick_stream_generator(
            client_id="cid",
            session_id="sess-1",
            text="hola",
            model_id=None,
            system_prompt_extra="",
        )
    )

    call_kwargs = mock_inference.infer.call_args.kwargs
    system_prompt = call_kwargs["params"]["system_prompt"]
    assert system_prompt.strip().startswith(QUICK_SYSTEM_PROMPT.strip())
```

- [ ] **Step 2: Verificar que fallan**

```bash
cd /home/sito/jota-orchestrator
pytest tests/unit/test_quick_system_prompt.py -v
```
Esperado: FAIL — `_quick_stream_generator` no acepta `system_prompt_extra` aún.

- [ ] **Step 3: Implementar en jota-orchestrator/src/api/quick.py**

**3a — Añadir campo a QuickRequest:**

```python
class QuickRequest(BaseModel):
    """Petición para el endpoint QUICK."""
    text: str
    model_id: Optional[str] = None
    system_prompt_extra: Optional[str] = None
```

**3b — Añadir parámetro a `_quick_stream_generator`:**

```python
async def _quick_stream_generator(
    client_id: str,
    session_id: str,
    text: str,
    model_id: Optional[str],
    system_prompt_extra: Optional[str] = None,
) -> AsyncGenerator[str, None]:
```

**3c — Anexar al `full_prompt` (justo después del bloque de `tool_instructions`):**

```python
    tool_instructions = tool_manager.get_system_prompt_addition(client_id=client_id)
    full_prompt = f"{QUICK_SYSTEM_PROMPT}\n"
    if tool_instructions:
        full_prompt += f"\n{tool_instructions}\n"
    if system_prompt_extra:
        full_prompt += f"\n{system_prompt_extra}\n"
```

**3d — Pasar el campo desde el endpoint:**

```python
    return StreamingResponse(
        _quick_stream_generator(
            client_id=client_id,
            session_id=session_id,
            text=request.text,
            model_id=request.model_id,
            system_prompt_extra=request.system_prompt_extra,
        ),
        media_type="application/x-ndjson"
    )
```

- [ ] **Step 4: Verificar que pasan**

```bash
cd /home/sito/jota-orchestrator
pytest tests/unit/test_quick_system_prompt.py -v
```
Esperado: 3 tests PASSED.

- [ ] **Step 5: Commit en jota-orchestrator**

```bash
cd /home/sito/jota-orchestrator
git add src/api/quick.py tests/unit/test_quick_system_prompt.py
git commit -m "feat: QuickRequest accepts system_prompt_extra — appended to system prompt"
```

- [ ] **Step 6: Crear PR en jota-orchestrator**

```bash
gh pr create \
  --title "feat: QuickRequest accepts system_prompt_extra" \
  --body "$(cat <<'EOF'
## Summary

- `QuickRequest` adds optional `system_prompt_extra: str | None`
- `_quick_stream_generator` appends it to `full_prompt` when set (empty string is ignored)
- Enables per-client system prompt customization via `ClientConfig.system_prompt_extra`

## Test plan

- [ ] `pytest tests/unit/test_quick_system_prompt.py` — 3 tests: con valor, None, string vacío

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Task 1: TTSClient.connect() acepta voice y speed

**Files:**
- Modify: `src/services/tts_client.py:26-46`
- Create: `tests/unit/test_tts_connect.py`

- [ ] **Step 1: Escribir los tests que fallarán**

```python
# tests/unit/test_tts_connect.py
"""Tests for TTSClient.connect() — auth handshake with voice/speed."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.tts_client import TTSClient


def _make_ws(auth_response: dict):
    ws = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps(auth_response))
    ws.send = AsyncMock()
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=False)
    return ws


@pytest.fixture
def mock_connect():
    """Patch websockets.connect to return a controllable ws mock."""
    ws = _make_ws({"type": "auth_ok"})
    with patch("src.services.tts_client.websockets.connect", return_value=ws) as p:
        yield p, ws


async def test_connect_sends_token_only_when_no_voice_speed(mock_connect):
    """If voice/speed are None, auth message contains only token."""
    _, ws = mock_connect
    client = TTSClient(url="localhost:8005", token="key123", client_id="cid")

    await client.connect()

    sent = json.loads(ws.send.call_args[0][0])
    assert sent == {"type": "auth", "token": "key123"}


async def test_connect_includes_voice_when_provided(mock_connect):
    _, ws = mock_connect
    client = TTSClient(url="localhost:8005", token="key123", client_id="cid")

    await client.connect(voice="af_heart")

    sent = json.loads(ws.send.call_args[0][0])
    assert sent["voice"] == "af_heart"
    assert sent["token"] == "key123"


async def test_connect_includes_speed_when_provided(mock_connect):
    _, ws = mock_connect
    client = TTSClient(url="localhost:8005", token="key123", client_id="cid")

    await client.connect(speed=1.25)

    sent = json.loads(ws.send.call_args[0][0])
    assert sent["speed"] == 1.25


async def test_connect_includes_both_voice_and_speed(mock_connect):
    _, ws = mock_connect
    client = TTSClient(url="localhost:8005", token="key123", client_id="cid")

    await client.connect(voice="bf_emma", speed=0.9)

    sent = json.loads(ws.send.call_args[0][0])
    assert sent == {"type": "auth", "token": "key123", "voice": "bf_emma", "speed": 0.9}


async def test_connect_raises_on_auth_failure(mock_connect):
    _, ws = mock_connect
    ws.recv = AsyncMock(return_value=json.dumps({"type": "auth_error", "message": "bad key"}))
    client = TTSClient(url="localhost:8005", token="badkey", client_id="cid")

    with pytest.raises(RuntimeError, match="auth failed"):
        await client.connect()
```

- [ ] **Step 2: Verificar que fallan**

```bash
pytest tests/unit/test_tts_connect.py -v
```
Esperado: todos FAIL (algunos porque `connect()` no acepta `voice`/`speed`, otros porque el msg no incluye esos campos).

- [ ] **Step 3: Implementar en TTSClient**

```python
# src/services/tts_client.py — método connect()
async def connect(
    self,
    voice: Optional[str] = None,
    speed: Optional[float] = None,
) -> None:
    """Open WS and authenticate. Raises RuntimeError on auth failure."""
    ws_url = f"ws://{self.url}/ws"
    self.ws = await websockets.connect(ws_url)

    auth_msg: dict = {"type": "auth", "token": self.token}
    if voice is not None:
        auth_msg["voice"] = voice
    if speed is not None:
        auth_msg["speed"] = speed

    await self.ws.send(json.dumps(auth_msg))
    try:
        raw = await self.ws.recv()
    except ConnectionClosed as exc:
        raise RuntimeError(
            f"[{self.client_id}] TTS connection closed during auth: {exc}"
        ) from exc

    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(
            f"[{self.client_id}] TTS sent non-JSON during auth: {raw!r}"
        ) from exc

    if msg.get("type") != "auth_ok":
        raise RuntimeError(f"[{self.client_id}] TTS auth failed: {msg}")

    logger.info("[%s] Connected to TTS at ws://%s/ws", self.client_id, self.url)
```

- [ ] **Step 4: Verificar que pasan**

```bash
pytest tests/unit/test_tts_connect.py -v
```
Esperado: 5 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/services/tts_client.py tests/unit/test_tts_connect.py
git commit -m "feat: TTSClient.connect() accepts optional voice and speed params"
```

---

## Task 2: OrchestratorClient acepta system_prompt_extra

**Files:**
- Modify: `src/services/orchestrator_client.py:80-146`
- Create: `tests/unit/test_orchestrator_system_prompt.py`

- [ ] **Step 1: Escribir los tests que fallarán**

```python
# tests/unit/test_orchestrator_system_prompt.py
"""Tests for OrchestratorClient system_prompt_extra in payload."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.orchestrator_client import OrchestratorClient


def _make_client() -> OrchestratorClient:
    c = OrchestratorClient(
        base_url="localhost:8000",
        api_key="key",
        client_id="cid",
    )
    return c


def _mock_http(lines: list[str]):
    """Build an httpx mock that streams the given NDJSON lines."""
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()

    async def aiter_lines():
        for line in lines:
            yield line

    mock_response.aiter_lines = aiter_lines
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_http = AsyncMock()
    mock_http.stream = MagicMock(return_value=mock_response)
    return mock_http


async def test_stream_response_payload_has_no_system_prompt_extra_by_default():
    """system_prompt_extra is absent from payload when not provided."""
    c = _make_client()
    c._http = _mock_http(['{"type":"token","content":"hi"}'])

    events = [e async for e in c.stream_response("hello")]

    payload = json.loads(c._http.stream.call_args[1]["json"]
                         if "json" in c._http.stream.call_args[1]
                         else c._http.stream.call_args.kwargs["json"])
    assert "system_prompt_extra" not in payload


async def test_stream_response_payload_includes_system_prompt_extra_when_set():
    """system_prompt_extra appears in payload when provided."""
    c = _make_client()
    c._http = _mock_http(['{"type":"token","content":"hi"}'])

    events = [e async for e in c.stream_response("hello", system_prompt_extra="be brief")]

    payload = c._http.stream.call_args.kwargs["json"]
    assert payload["system_prompt_extra"] == "be brief"


async def test_stream_response_payload_omits_system_prompt_extra_when_none():
    """None explicitly passed → field not included."""
    c = _make_client()
    c._http = _mock_http(['{"type":"token","content":"hi"}'])

    events = [e async for e in c.stream_response("hello", system_prompt_extra=None)]

    payload = c._http.stream.call_args.kwargs["json"]
    assert "system_prompt_extra" not in payload


async def test_listen_loop_forwards_system_prompt_extra_to_stream_response():
    """listen_loop passes system_prompt_extra down to stream_response."""
    c = _make_client()
    c._http = _mock_http(['{"type":"token","content":"hi"}'])

    on_token = AsyncMock()
    on_event = AsyncMock()
    await c.listen_loop("hello", on_token=on_token, on_event=on_event, system_prompt_extra="be brief")

    payload = c._http.stream.call_args.kwargs["json"]
    assert payload["system_prompt_extra"] == "be brief"
```

- [ ] **Step 2: Verificar que fallan**

```bash
pytest tests/unit/test_orchestrator_system_prompt.py -v
```
Esperado: FAIL (los métodos no aceptan `system_prompt_extra` aún).

- [ ] **Step 3: Implementar en OrchestratorClient**

En `stream_response`, añadir el parámetro y el campo condicional al payload:

```python
async def stream_response(
    self,
    text: str,
    user_id: Optional[str] = None,
    model_id: Optional[str] = None,
    system_prompt_extra: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    # ... (docstring sin cambios) ...
    if not self._http:
        raise RuntimeError("OrchestratorClient no está conectado. Llama a connect() primero.")

    payload = {
        "text": text,
        "user_id": user_id or self.client_id,
    }
    if model_id:
        payload["model_id"] = model_id
    if system_prompt_extra is not None:
        payload["system_prompt_extra"] = system_prompt_extra

    # ... resto sin cambios ...
```

En `listen_loop`, añadir el parámetro y pasarlo a `stream_response`:

```python
async def listen_loop(
    self,
    text: str,
    on_token: Callable[[str], Awaitable[None]],
    on_event: Callable[[Dict[str, Any]], Awaitable[None]],
    user_id: Optional[str] = None,
    model_id: Optional[str] = None,
    system_prompt_extra: Optional[str] = None,
):
    async for event in self.stream_response(
        text,
        user_id=user_id,
        model_id=model_id,
        system_prompt_extra=system_prompt_extra,
    ):
        if event.get("type") == "token" and event.get("content") is not None:
            await on_token(event["content"])
        else:
            await on_event(event)
```

- [ ] **Step 4: Verificar que pasan**

```bash
pytest tests/unit/test_orchestrator_system_prompt.py -v
```
Esperado: 4 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/services/orchestrator_client.py tests/unit/test_orchestrator_system_prompt.py
git commit -m "feat: OrchestratorClient accepts system_prompt_extra in payload"
```

---

## Task 3: Bridge propaga ClientConfig a TTS, Orchestrator y barge-in

**Files:**
- Modify: `src/services/bridge.py:274, 298-354`
- Create: `tests/unit/test_bridge_config_propagation.py`
- Modify: `tests/unit/test_bridge_barge_in.py` (añadir 2 tests al final)

- [ ] **Step 1: Escribir el test de barge-in con config personalizada**

Añadir al final de `tests/unit/test_bridge_barge_in.py`:

```python
async def test_barge_in_uses_config_threshold_not_global(make_bridge):
    """Bridge uses config.barge_in_min_chars, not settings.BARGE_IN_MIN_CHARS."""
    from src.models.schemas import ClientConfig
    ws = AsyncMock()
    # config con umbral alto
    config = ClientConfig(barge_in_min_chars=50)
    bridge = JotaBridge(client=_CLIENT, config=config, client_ws=ws)
    bridge.handshake = Handshake(
        client_key="test-key", input_mode="audio", output_mode=["audio", "text", "status"]
    )
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    # 11 chars < 50 → no barge-in
    await bridge._on_transcription("hello world", False)

    calls = ws.send_json.call_args_list
    assert len(calls) == 1  # solo el partial, sin "interrupted"
    assert calls[0][0][0] == {"type": "transcription_partial", "text": "hello world"}
    bridge._active_turn.cancel()
    try:
        await bridge._active_turn
    except (asyncio.CancelledError, Exception):
        pass


async def test_barge_in_triggers_when_above_custom_threshold(make_bridge):
    """Barge-in fires when partial >= config.barge_in_min_chars."""
    from src.models.schemas import ClientConfig
    ws = AsyncMock()
    config = ClientConfig(barge_in_min_chars=3)
    bridge = JotaBridge(client=_CLIENT, config=config, client_ws=ws)
    bridge.handshake = Handshake(
        client_key="test-key", input_mode="audio", output_mode=["audio", "text", "status"]
    )
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    await bridge._on_transcription("hola", False)  # 4 chars >= 3

    calls = ws.send_json.call_args_list
    assert any(c[0][0].get("type") == "interrupted" for c in calls)
```

- [ ] **Step 2: Escribir los tests de propagación de config**

```python
# tests/unit/test_bridge_config_propagation.py
"""Tests: bridge passes ClientConfig values to TTS and Orchestrator."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from src.services.bridge import JotaBridge
from src.models.schemas import Client, ClientConfig, Handshake

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)


def _make_bridge(config: ClientConfig, output_mode=None):
    if output_mode is None:
        output_mode = ["audio", "text", "status"]
    ws = AsyncMock()
    bridge = JotaBridge(client=_CLIENT, config=config, client_ws=ws)
    bridge.handshake = Handshake(
        client_key="test-key",
        input_mode="audio",
        output_mode=output_mode,
    )
    bridge.orchestrator = AsyncMock()
    bridge.orchestrator.listen_loop = AsyncMock()
    return bridge


async def test_call_orchestrator_passes_preferred_model_id():
    """bridge passes config.preferred_model_id to orchestrator.listen_loop."""
    config = ClientConfig(preferred_model_id="llama3-70b")
    bridge = _make_bridge(config)

    await bridge._call_orchestrator("hola")

    _, kwargs = bridge.orchestrator.listen_loop.call_args
    assert kwargs.get("model_id") == "llama3-70b"


async def test_call_orchestrator_passes_none_model_id_when_not_set():
    """bridge passes model_id=None when preferred_model_id is not set."""
    config = ClientConfig(preferred_model_id=None)
    bridge = _make_bridge(config)

    await bridge._call_orchestrator("hola")

    _, kwargs = bridge.orchestrator.listen_loop.call_args
    assert kwargs.get("model_id") is None


async def test_call_orchestrator_passes_system_prompt_extra():
    """bridge passes config.system_prompt_extra to orchestrator.listen_loop."""
    config = ClientConfig(system_prompt_extra="responde siempre en inglés")
    bridge = _make_bridge(config)

    await bridge._call_orchestrator("hola")

    _, kwargs = bridge.orchestrator.listen_loop.call_args
    assert kwargs.get("system_prompt_extra") == "responde siempre en inglés"


async def test_call_orchestrator_passes_none_system_prompt_when_not_set():
    config = ClientConfig(system_prompt_extra=None)
    bridge = _make_bridge(config)

    await bridge._call_orchestrator("hola")

    _, kwargs = bridge.orchestrator.listen_loop.call_args
    assert kwargs.get("system_prompt_extra") is None


async def test_call_orchestrator_passes_voice_and_speed_to_tts():
    """bridge passes config.tts_voice and config.tts_speed to TTSClient.connect()."""
    config = ClientConfig(tts_voice="bf_emma", tts_speed=1.2)
    bridge = _make_bridge(config, output_mode=["audio", "text"])

    mock_tts = AsyncMock()
    mock_tts.connect = AsyncMock()
    mock_tts.end = AsyncMock()
    mock_tts.close = AsyncMock()
    mock_tts.get_audio_stream = AsyncMock(return_value=aiter([]))

    with patch("src.services.bridge.TTSClient", return_value=mock_tts):
        await bridge._call_orchestrator("hola")

    mock_tts.connect.assert_called_once_with(voice="bf_emma", speed=1.2)


async def aiter(items):
    """Helper: sync list → async generator."""
    for item in items:
        yield item
```

- [ ] **Step 3: Verificar que los nuevos tests fallan**

```bash
pytest tests/unit/test_bridge_barge_in.py::test_barge_in_uses_config_threshold_not_global \
       tests/unit/test_bridge_barge_in.py::test_barge_in_triggers_when_above_custom_threshold \
       tests/unit/test_bridge_config_propagation.py -v
```
Esperado: todos FAIL.

- [ ] **Step 4: Implementar los cambios en bridge.py**

**Cambio 1** — `_on_transcription()`, línea ~274. Sustituir:
```python
if len(text) >= settings.BARGE_IN_MIN_CHARS:
```
por:
```python
if len(text) >= self.config.barge_in_min_chars:
```

**Cambio 2** — `_call_orchestrator()`. Sustituir el bloque de creación de `tts`:
```python
        tts: Optional[TTSClient] = None
        if needs_audio:
            tts = TTSClient(
                url=settings.TTS_WS_URL,
                token=settings.TTS_TOKEN,
                client_id=self.client_id,
            )
            await tts.connect()
```
por:
```python
        tts: Optional[TTSClient] = None
        if needs_audio:
            tts = TTSClient(
                url=settings.TTS_WS_URL,
                token=settings.TTS_TOKEN,
                client_id=self.client_id,
            )
            await tts.connect(
                voice=self.config.tts_voice,
                speed=self.config.tts_speed,
            )
```

**Cambio 3** — `_call_orchestrator()`. Sustituir la llamada a `listen_loop`:
```python
            await self.orchestrator.listen_loop(
                text=text,
                on_token=_on_token,
                on_event=_on_event,
            )
```
por:
```python
            await self.orchestrator.listen_loop(
                text=text,
                on_token=_on_token,
                on_event=_on_event,
                model_id=self.config.preferred_model_id,
                system_prompt_extra=self.config.system_prompt_extra,
            )
```

- [ ] **Step 5: Verificar que pasan todos los tests (nuevos + regresión)**

```bash
pytest tests/unit/ -v
```
Esperado: todos PASSED. Ningún test previo roto.

- [ ] **Step 6: Commit**

```bash
git add src/services/bridge.py \
        tests/unit/test_bridge_barge_in.py \
        tests/unit/test_bridge_config_propagation.py
git commit -m "feat: propagate ClientConfig to TTS, Orchestrator, and barge-in (Fase 3)"
```

---

## Task 4: PR y cierre de issue

- [ ] **Step 1: Crear el PR**

```bash
gh pr create \
  --title "feat: Fase 3 — propagate ClientConfig to TTS, Orchestrator, barge-in" \
  --body "$(cat <<'EOF'
## Summary

- `TTSClient.connect()` now accepts `voice` and `speed` → sent in the auth handshake
- `OrchestratorClient.stream_response()` / `listen_loop()` accept `system_prompt_extra` → included in payload when set
- `JotaBridge._call_orchestrator()` passes `config.tts_voice`, `config.tts_speed`, `config.preferred_model_id`, `config.system_prompt_extra` from the resolved `ClientConfig`
- `JotaBridge._on_transcription()` uses `config.barge_in_min_chars` instead of the global `settings.BARGE_IN_MIN_CHARS`

## Test plan

- [ ] `pytest tests/unit/test_tts_connect.py` — 5 tests: auth msg con/sin voice/speed
- [ ] `pytest tests/unit/test_orchestrator_system_prompt.py` — 4 tests: payload con/sin system_prompt_extra
- [ ] `pytest tests/unit/test_bridge_config_propagation.py` — 5 tests: propagación completa en bridge
- [ ] `pytest tests/unit/test_bridge_barge_in.py` — regresión + 2 nuevos tests de umbral per-cliente
- [ ] `pytest tests/unit/ -v` — full suite green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Mergear y actualizar `../.github/TASKS.md`**

Marcar Fase 3 como ✅ COMPLETADA en `/home/sito/.github/TASKS.md`.
