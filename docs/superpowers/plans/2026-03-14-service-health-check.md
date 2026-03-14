# Service Health Check Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pre-session health checks that ping each microservice after handshake, send structured `service_status` JSON to the client for unavailable services, and abort the session on critical failures.

**Architecture:** Each client class gains a `ping()` method that calls `GET /health` on its service and returns `bool`. `JotaBridge` gains `health_check()` which calls each ping, applies criticality rules, and notifies the client. `routes.py` calls `health_check()` between `connect_internal_services()` and `run()`.

**Tech Stack:** Python 3.11, httpx (already a dependency), pytest-asyncio (asyncio_mode=auto configured in pytest.ini), unittest.mock.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `src/services/orchestrator_client.py` | Add `async def ping() -> bool` |
| Modify | `src/services/tts_client.py` | Add `@staticmethod async def ping(url) -> bool` + `import re` |
| Modify | `src/services/bridge.py` | Add `async def health_check() -> bool` |
| Modify | `src/api/routes.py` | Call `health_check()` between connect and run |
| Create | `tests/unit/__init__.py` | Package marker |
| Create | `tests/unit/test_orchestrator_ping.py` | Unit tests for `OrchestratorClient.ping()` |
| Create | `tests/unit/test_tts_ping.py` | Unit tests for `TTSClient.ping()` |
| Create | `tests/unit/test_bridge_health_check.py` | Unit tests for `JotaBridge.health_check()` |

---

## Chunk 1: Client ping() methods

### Task 1: `OrchestratorClient.ping()`

**Files:**
- Modify: `src/services/orchestrator_client.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/test_orchestrator_ping.py`

- [ ] **Step 1: Create test package and write failing tests**

Create `tests/unit/__init__.py` (empty).

Create `tests/unit/test_orchestrator_ping.py`:

```python
"""Tests for OrchestratorClient.ping()."""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock
from src.services.orchestrator_client import OrchestratorClient


@pytest.fixture
def client():
    c = OrchestratorClient(
        base_url="http://localhost:8000",
        api_key="test-key",
        client_id="test",
    )
    c._http = AsyncMock(spec=httpx.AsyncClient)
    return c


async def test_ping_returns_true_on_200(client):
    response = MagicMock()
    response.is_success = True
    client._http.get = AsyncMock(return_value=response)

    result = await client.ping()

    assert result is True
    client._http.get.assert_called_once_with(
        "http://localhost:8000/health", timeout=5.0
    )


async def test_ping_returns_false_on_503(client):
    response = MagicMock()
    response.is_success = False
    client._http.get = AsyncMock(return_value=response)

    result = await client.ping()

    assert result is False


async def test_ping_returns_false_on_network_error(client):
    client._http.get = AsyncMock(
        side_effect=httpx.ConnectError("connection refused")
    )

    result = await client.ping()

    assert result is False
```

- [ ] **Step 2: Run tests — verify they FAIL**

```bash
pytest tests/unit/test_orchestrator_ping.py -v
```

Expected: `AttributeError: 'OrchestratorClient' object has no attribute 'ping'`

- [ ] **Step 3: Implement `ping()` in `OrchestratorClient`**

In `src/services/orchestrator_client.py`, add after the `close()` method:

```python
async def ping(self) -> bool:
    """Return True if the orchestrator /health endpoint responds with 2xx."""
    try:
        r = await self._http.get(f"{self.base_url}/health", timeout=5.0)
        return r.is_success
    except Exception:
        return False
```

- [ ] **Step 4: Run tests — verify they PASS**

```bash
pytest tests/unit/test_orchestrator_ping.py -v
```

Expected: 3 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/services/orchestrator_client.py tests/unit/__init__.py tests/unit/test_orchestrator_ping.py
git commit -m "feat: add OrchestratorClient.ping()"
```

---

### Task 2: `TTSClient.ping()`

**Files:**
- Modify: `src/services/tts_client.py`
- Create: `tests/unit/test_tts_ping.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_tts_ping.py`:

```python
"""Tests for TTSClient.ping() static method."""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.tts_client import TTSClient


async def test_ping_ws_url_hits_http_health():
    """ws://host:port/path → GET http://host:port/health"""
    mock_response = MagicMock()
    mock_response.is_success = True

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.tts_client.httpx.AsyncClient", return_value=mock_client):
        result = await TTSClient.ping("ws://localhost:8005/ws")

    assert result is True
    mock_client.get.assert_called_once_with("http://localhost:8005/health", timeout=5.0)


async def test_ping_wss_url_hits_https_health():
    """wss://host/path → GET https://host/health"""
    mock_response = MagicMock()
    mock_response.is_success = True

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.tts_client.httpx.AsyncClient", return_value=mock_client):
        result = await TTSClient.ping("wss://tts.example.com/synthesize")

    assert result is True
    mock_client.get.assert_called_once_with("https://tts.example.com/health", timeout=5.0)


async def test_ping_returns_false_on_503():
    mock_response = MagicMock()
    mock_response.is_success = False

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.tts_client.httpx.AsyncClient", return_value=mock_client):
        result = await TTSClient.ping("ws://localhost:8005/ws")

    assert result is False


async def test_ping_returns_false_on_empty_url():
    """Triggering incident: TTS_WS_URL='' must not crash, must return False."""
    result = await TTSClient.ping("")
    assert result is False


async def test_ping_returns_false_on_network_error():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.tts_client.httpx.AsyncClient", return_value=mock_client):
        result = await TTSClient.ping("ws://localhost:8005/ws")

    assert result is False
```

- [ ] **Step 2: Run tests — verify they FAIL**

```bash
pytest tests/unit/test_tts_ping.py -v
```

Expected: `AttributeError: type object 'TTSClient' has no attribute 'ping'`

- [ ] **Step 3: Implement `ping()` in `TTSClient`**

At the top of `src/services/tts_client.py`, add two module-level imports alongside the existing ones:

```python
import re
import httpx
```

`httpx` must be at module level (not local to the method) so that `patch("src.services.tts_client.httpx.AsyncClient", ...)` in the tests can intercept it correctly.

Add this static method after `close()`:

```python
@staticmethod
async def ping(url: str) -> bool:
    """Return True if the TTS /health endpoint responds with 2xx.

    Converts ws://host:port/path → http://host:port/health
             wss://host/path    → https://host/health
    Empty or malformed URLs return False (caught by the except clause).
    """
    http_base = re.sub(r"^ws", "http", url.split("?")[0])  # ws→http, wss→https
    http_base = "/".join(http_base.split("/")[:3])          # scheme+host+port only
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{http_base}/health", timeout=5.0)
            return r.is_success
    except Exception:
        return False
```

- [ ] **Step 4: Run tests — verify they PASS**

```bash
pytest tests/unit/test_tts_ping.py -v
```

Expected: 5 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/services/tts_client.py tests/unit/test_tts_ping.py
git commit -m "feat: add TTSClient.ping() static method"
```

---

## Chunk 2: Bridge health_check() and routes integration

### Task 3: `JotaBridge.health_check()`

**Files:**
- Modify: `src/services/bridge.py`
- Create: `tests/unit/test_bridge_health_check.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_bridge_health_check.py`:

```python
"""Tests for JotaBridge.health_check()."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.bridge import JotaBridge
from src.models.schemas import Handshake


@pytest.fixture
def make_bridge():
    """Factory: returns a bridge with mocked ws, orchestrator, and transcriber."""
    def _make(input_mode="audio", output_mode=None):
        if output_mode is None:
            output_mode = ["audio", "text", "status"]
        ws = AsyncMock()
        bridge = JotaBridge(client_id="test", client_ws=ws)
        bridge.handshake = Handshake(input_mode=input_mode, output_mode=output_mode)
        bridge.orchestrator = AsyncMock()
        bridge.orchestrator.ping = AsyncMock(return_value=True)
        bridge.transcriber = MagicMock()
        bridge.transcriber._is_ready = True
        return bridge
    return _make


# --- Orchestrator checks ---

async def test_health_check_passes_when_all_ok(make_bridge):
    bridge = make_bridge()

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=True)):
        result = await bridge.health_check()

    assert result is True
    bridge.client_ws.send_json.assert_not_called()


async def test_health_check_fails_when_orchestrator_down(make_bridge):
    bridge = make_bridge()
    bridge.orchestrator.ping = AsyncMock(return_value=False)

    result = await bridge.health_check()

    assert result is False
    bridge.client_ws.send_json.assert_called_once_with({
        "type": "service_status",
        "service": "orchestrator",
        "status": "unavailable",
        "message": "Orchestrator unavailable, closing session",
    })


# --- Transcriber checks ---

async def test_health_check_fails_when_transcriber_not_ready(make_bridge):
    bridge = make_bridge(input_mode="audio")
    bridge.transcriber._is_ready = False

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=True)):
        result = await bridge.health_check()

    assert result is False
    bridge.client_ws.send_json.assert_called_once_with({
        "type": "service_status",
        "service": "transcriber",
        "status": "unavailable",
        "message": "Transcriber unavailable, closing session",
    })


async def test_health_check_fails_when_transcriber_is_none(make_bridge):
    """transcriber=None with audio input must fail (primary path is caught upstream,
    but this guards the race condition / defense-in-depth branch)."""
    bridge = make_bridge(input_mode="audio")
    bridge.transcriber = None

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=True)):
        result = await bridge.health_check()

    assert result is False
    bridge.client_ws.send_json.assert_called_once_with({
        "type": "service_status",
        "service": "transcriber",
        "status": "unavailable",
        "message": "Transcriber unavailable, closing session",
    })


async def test_health_check_skips_transcriber_check_for_text_input(make_bridge):
    """If input_mode is text, transcriber state is irrelevant."""
    bridge = make_bridge(input_mode="text")
    bridge.transcriber._is_ready = False  # would fail if checked

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=True)):
        result = await bridge.health_check()

    assert result is True


# --- TTS checks ---

async def test_health_check_warns_but_continues_when_tts_down(make_bridge):
    bridge = make_bridge(output_mode=["audio", "text"])

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=False)):
        result = await bridge.health_check()

    assert result is True  # session continues
    bridge.client_ws.send_json.assert_called_once_with({
        "type": "service_status",
        "service": "tts",
        "status": "unavailable",
        "message": "Audio output unavailable",
    })


async def test_health_check_skips_tts_check_when_no_audio_output(make_bridge):
    """If output_mode has no 'audio', TTS is not pinged."""
    bridge = make_bridge(output_mode=["text", "status"])

    with patch("src.services.bridge.TTSClient.ping", new=AsyncMock(return_value=False)) as mock_ping:
        result = await bridge.health_check()

    assert result is True
    mock_ping.assert_not_called()
```

- [ ] **Step 2: Run tests — verify they FAIL**

```bash
pytest tests/unit/test_bridge_health_check.py -v
```

Expected: `AttributeError: 'JotaBridge' object has no attribute 'health_check'`

- [ ] **Step 3: Implement `health_check()` in `JotaBridge`**

In `src/services/bridge.py`, add after `close_all()` and before `run()`:

```python
async def health_check(self) -> bool:
    """Ping each microservice and notify the client of any issues.

    Returns True if the session can proceed, False if a critical service
    is unavailable (caller should close the WebSocket).
    """
    # Orchestrator — always critical
    if not await self.orchestrator.ping():
        await self.client_ws.send_json({
            "type": "service_status",
            "service": "orchestrator",
            "status": "unavailable",
            "message": "Orchestrator unavailable, closing session",
        })
        return False

    # Transcriber — critical only for audio input (defense-in-depth;
    # primary failure path is caught by connect_internal_services → routes.py)
    if self.handshake.input_mode == "audio":
        if not self.transcriber or not self.transcriber._is_ready:
            await self.client_ws.send_json({
                "type": "service_status",
                "service": "transcriber",
                "status": "unavailable",
                "message": "Transcriber unavailable, closing session",
            })
            return False

    # TTS — non-critical; session continues in degraded mode
    if "audio" in self.handshake.output_mode:
        if not await TTSClient.ping(settings.TTS_WS_URL):
            await self.client_ws.send_json({
                "type": "service_status",
                "service": "tts",
                "status": "unavailable",
                "message": "Audio output unavailable",
            })

    return True
```

- [ ] **Step 4: Run the full unit test suite**

```bash
pytest tests/unit/ -v
```

Expected: all tests PASS (including the two from previous tasks).

- [ ] **Step 5: Commit**

```bash
git add src/services/bridge.py tests/unit/test_bridge_health_check.py
git commit -m "feat: add JotaBridge.health_check()"
```

---

### Task 4: Wire `health_check()` into `routes.py`

**Files:**
- Modify: `src/api/routes.py`

No new test file — the behaviour is covered by the bridge unit tests. This task is a pure wiring change.

- [ ] **Step 1: Add `health_check()` call in `routes.py`**

In `src/api/routes.py`, replace the existing block:

```python
    try:
         # Tira las conexiones concurrentes a Orchestrator, Transcriber, TTS
         await bridge.connect_internal_services()
    except Exception as e:
         logger.error(f"[{client_id}] Fallo al inicializar puentes internos red docker. {e}")
         await websocket.close(code=1011, reason="Problema estableciendo microservicios internos del hub.")
         return

    # 3. LANZAR LOOPS CONCURRENTES
```

With:

```python
    try:
         # Tira las conexiones concurrentes a Orchestrator, Transcriber, TTS
         await bridge.connect_internal_services()
    except Exception as e:
         logger.error(f"[{client_id}] Fallo al inicializar puentes internos red docker. {e}")
         await websocket.close(code=1011, reason="Problema estableciendo microservicios internos del hub.")
         return

    # 2.5 HEALTH CHECK — verifica disponibilidad de servicios antes de abrir la sesión
    if not await bridge.health_check():
        logger.warning(f"[{client_id}] Health check falló. Cerrando sesión.")
        await websocket.close(code=1011, reason="Servicio crítico no disponible.")
        return

    # 3. LANZAR LOOPS CONCURRENTES
```

- [ ] **Step 2: Run the full test suite to confirm nothing broke**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add src/api/routes.py
git commit -m "feat: wire health_check() into gateway session startup"
```

---

## Manual smoke test (optional, requires services running)

To verify end-to-end with the actual services:

1. Start the gateway with `TTS_WS_URL=""` in `.env`
2. Connect a client with `output_mode: ["audio", "text"]`
3. Expect to receive before the session starts:
   ```json
   {"type": "service_status", "service": "tts", "status": "unavailable", "message": "Audio output unavailable"}
   ```
4. Verify text responses still work normally after the warning.
