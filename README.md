# jota-gateway

BFF (Backend For Frontend) del ecosistema Jota IA. Actúa como punto de entrada único para los clientes (ESP32, web, app) y enruta los mensajes hacia los microservicios internos: orquestador, transcriptor y TTS.

```
Cliente (ESP32 / Web)
        │  WebSocket ws://gateway:8004/ws/stream/{client_id}
        ▼
  jota-gateway  ──► JotaOrchestrator  (HTTP NDJSON streaming)
                ──► JotaTranscriber   (WebSocket, PCM Float32)
                ──► jota-speaker TTS  (WebSocket, PCM16)
```

---

## Endpoints

| Endpoint | Protocolo | Descripción |
|---|---|---|
| `/ws/stream/{client_id}` | WebSocket | Sesión interactiva completa (texto + audio) |
| `/transcribe` | HTTP POST | Transcripción one-shot de un archivo de audio |
| `/health` | HTTP GET | Estado del servicio |

---

## Quick start

```bash
# Instalar dependencias
pip install -r requirements.txt

# Arrancar en desarrollo
uvicorn src.main:app --host 0.0.0.0 --port 8004 --reload

# Con Docker
docker compose up
```

---

## Configuración

Todas las variables en `.env` (ver `.env.sample`):

| Variable | Default | Descripción |
|---|---|---|
| `ORCHESTRATOR_BASE_URL` | `http://localhost:8000` | URL del JotaOrchestrator |
| `ORCHESTRATOR_API_KEY` | `jota_internal_default_key` | Clave de cliente para el orquestador |
| `TRANSCRIBER_WS_URL` | `ws://localhost:9000` | WebSocket del JotaTranscriber |
| `TTS_WS_URL` | `ws://localhost:8005/ws` | WebSocket de jota-speaker |
| `TTS_TOKEN` | `gateway` | Token de autenticación para jota-speaker |

---

## Protocolo de cliente

Ver [`docs/client-protocol.md`](docs/client-protocol.md) para la guía completa de integración, incluyendo el ciclo de vida de la sesión, formato de mensajes y manejo de audio.

---

## Arquitectura interna

El gateway instancia un `JotaBridge` por cada cliente conectado. El bridge coordina:

- **OrchestratorClient** — siempre activo; envía prompts y recibe tokens en streaming NDJSON.
- **TranscriberClient** — activo solo si `input_mode = "audio"`; recibe PCM Float32 del cliente y devuelve transcripciones finales.
- **TTSClient** — creado por petición (no persistente); se conecta a jota-speaker, envía tokens, recibe audio PCM16 y lo reenvía al cliente.
