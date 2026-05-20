# OpenClaw — Configuration Schema (`openclaw.json`)

Location: `~/.openclaw/openclaw.json`

## Full Schema Reference

```jsonc
{
  // ── Models ──────────────────────────────────────────────────────
  "models": {
    "default": "anthropic/claude-sonnet-4-5",   // primary model

    // Cloud providers
    "anthropic": { "apiKey": "sk-ant-..." },
    "openai":    { "apiKey": "sk-..." },
    "google":    { "apiKey": "..." },
    "groq":      { "apiKey": "..." },

    // Local
    "ollama": { "baseUrl": "http://localhost:11434" },

    // Fallback chain
    "fallback": ["anthropic/claude-haiku-4-5", "openai/gpt-4o-mini"]
  },

  // ── Gateway ──────────────────────────────────────────────────────
  "gateway": {
    "port": 18789,
    "bind": "loopback",                         // loopback | 0.0.0.0 | specific IP

    "auth": {
      "mode": "token",                          // token | password | trusted-proxy | none
      "token": "long-random-string",            // used with mode: token
      "password": "${OPENCLAW_GATEWAY_PASSWORD}" // used with mode: password
    },

    "controlUi": { "enabled": true }
  },

  // ── Channels ──────────────────────────────────────────────────────
  "channels": {
    "telegram": {
      "botToken": "...",
      "allowFrom": ["@username"],               // optional allowlist
    },
    "whatsapp": {
      "allowFrom": ["+34600000000"],
      "groups": { "*": { "requireMention": true } }
    },
    "discord": {
      "token": "...",
      "allowFrom": ["userid1"]
    },
    "signal": { /* requires signal-cli */ },
    "webchat": { "enabled": true }              // built-in, always at :18789
  },

  // ── Tools ──────────────────────────────────────────────────────
  "tools": {
    "profile": "coding",                       // full | coding | messaging | minimal
    "allow":   ["group:fs", "browser", "web_search"],
    "deny":    ["exec"]                        // deny always wins over allow
  },

  // ── Multi-agent routing ──────────────────────────────────────────
  "agents": {
    "list": [
      {
        "name": "main",
        "workspace": "~/.openclaw/workspaces/main",
        "tools": { "profile": "coding" },
        "channels": { "allowFrom": ["*"] }     // which channels this agent handles
      }
    ]
  },

  // ── Messages behavior ────────────────────────────────────────────
  "messages": {
    "groupChat": { "mentionPatterns": ["@openclaw"] }
  },

  // ── Skills extra dirs ────────────────────────────────────────────
  "skills": {
    "load": {
      "extraDirs": ["/path/to/extra/skills"]
    }
  }
}
```

## Partial Updates (preferred)

```bash
# Via CLI
openclaw config set models.default "anthropic/claude-opus-4-5"

# Via gateway tool (from agent)
# config.patch — only updates specified keys, never replaces unset fields
```

**Never** change `tools.exec.ask` or `tools.exec.security` via `config.patch` (protected paths).

## Environment Variables

| Variable | Equivalent config key |
|----------|-----------------------|
| `OPENCLAW_GATEWAY_TOKEN` | `gateway.auth.token` |
| `OPENCLAW_GATEWAY_PASSWORD` | `gateway.auth.password` |
| `OPENCLAW_GATEWAY_PORT` | `gateway.port` |
| `OPENCLAW_GATEWAY_BIND` | `gateway.bind` |
| `ANTHROPIC_API_KEY` | `models.anthropic.apiKey` |
| `OPENAI_API_KEY` | `models.openai.apiKey` |

## Config Precedence

CLI flags → Environment variables → `openclaw.json` → defaults
