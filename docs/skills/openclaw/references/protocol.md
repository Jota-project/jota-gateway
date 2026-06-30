# OpenClaw — Protocol Reference

## WebSocket Protocol

### Connection Flow

```
Client                          Gateway
  │                                │
  │◄── connect.challenge event ────┤
  │    {nonce, ts}                 │
  │                                │
  ├─── connect req ───────────────►│
  │    (with nonce in device.nonce) │
  │                                │
  │◄── res(ok) + hello-ok event ───┤
  │    {presence, health, policy}  │
  │                                │
  │◄══► message exchange ══════════╪══► agent responds with events
```

### Connect Frame (first frame, mandatory)

```json
{
  "type": "req",
  "id": "unique-request-id",
  "method": "connect",
  "params": {
    "minProtocol": 3,
    "maxProtocol": 4,
    "client": {
      "id": "my-bridge",
      "version": "1.0.0",
      "platform": "linux",
      "mode": "operator"
    },
    "role": "operator",
    "scopes": ["operator.read", "operator.write"],
    "auth": { "token": "<gateway_token>" },
    "device": {
      "id": "device-fingerprint",
      "publicKey": "...",
      "signature": "...",
      "signedAt": 1737264000000,
      "nonce": "<nonce-from-challenge>"
    }
  }
}
```

### Message Format

After connect:

```json
// Request
{"type": "req", "id": "<uuid>", "method": "<method>", "params": {...}}

// Response
{"type": "res", "id": "<uuid>", "ok": true, "payload": {...}}

// Event (server push)
{"type": "event", "event": "<name>", "payload": {...}, "seq": 42, "stateVersion": "..."}
```

### Key Methods

| Method | Description |
|--------|-------------|
| `connect` | Establish session (first frame) |
| `send` | Direct delivery outside chat runner |
| `agent` | Invoke agent (use `deliver: true` for delivery) |
| `chat.send` | Send message via chat runner |
| `chat.history` | Retrieve normalized transcripts |
| `sessions.send` | Send to specific session |
| `sessions.abort` | Cancel running turn |

### Streaming Response (protocol v4)

Agent responses stream via events before the final `res`:

```json
// Incremental token event
{"type": "event", "event": "agent", "payload": {"deltaText": "hello", "replace": false}}

// Replace (non-prefix edit)
{"type": "event", "event": "agent", "payload": {"deltaText": "full new text", "replace": true}}

// Final response
{"type": "res", "id": "...", "ok": true, "payload": {...}}
```

Response is complete when `type: "res"` with `ok: true` is received.

### Idempotency

Methods with side effects (`send`, `agent`) accept an idempotency key:
```json
{"type": "req", "id": "...", "method": "agent", "params": {"idempotencyKey": "uuid-v4", ...}}
```

---

## REST API (OpenAI-compatible)

### Authentication

All REST endpoints require:
```
Authorization: Bearer <token>
```

Where `<token>` = `gateway.auth.token` from `openclaw.json`.

Optional scope header (omit to use default operator scopes):
```
x-openclaw-scopes: operator.read,operator.talk
```

> **Important:** Unauthenticated requests return `404 Not Found`, not `401 Unauthorized`.

### Endpoints

```
GET  /v1/models                  → list available models
GET  /v1/models/{id}             → model details
POST /v1/chat/completions        → chat (OpenAI-compatible)
POST /v1/embeddings              → embeddings
POST /v1/responses               → responses API
```

### Chat Completions

```bash
curl -X POST http://localhost:18789/v1/chat/completions \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "default",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'
```

For streaming: `"stream": true` → SSE (Server-Sent Events), same format as OpenAI.

### Trusted-Proxy Mode

For services co-located with the gateway (e.g., jota-gateway on same host):

```json5
// openclaw.json
{
  "gateway": {
    "auth": {
      "mode": "trusted-proxy"
    }
  }
}
```

Loopback connections (`127.0.0.1`) auto-approve without a token.  
Useful when jota-gateway is the only client and runs on the same machine.
