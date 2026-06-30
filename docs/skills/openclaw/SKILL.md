---
name: openclaw
description: >
  Expert skill for OpenClaw — the self-hosted AI agent gateway (MIT, ex-Clawdbot/Moltbot).
  Activate when the user mentions: OpenClaw, openclaw.json, ClawHub, the OpenClaw Gateway,
  skills/plugins, multi-agent routing, SOUL.md, HEARTBEAT.md, openclaw CLI, channels
  (WhatsApp, Telegram, Discord, Signal, WebChat…), jota-voice project, the HA bridge, or
  anything related to configuring, running, or integrating OpenClaw.
---

# OpenClaw — Expert Skill

Self-hosted AI agent gateway (MIT) that connects messaging apps to an always-on AI agent.
Created by Peter Steinberger. Previously: Clawdbot (Nov 2025), Moltbot (Jan 2026).

Docs: https://docs.openclaw.ai/ · GitHub: https://github.com/openclaw/openclaw · ClawHub: https://clawhub.ai

---

## Quick Mental Model

```
User (WhatsApp / Telegram / Discord / WebChat / …)
        │
        ▼
  OpenClaw Gateway :18789   ←── ~/.openclaw/openclaw.json
  (Node 24 daemon)
        │
   ┌────┴──────────────────────────┐
   │ Agent Runtime                  │
   │  SOUL.md · HEARTBEAT.md        │
   │  Skills (SKILL.md files)       │
   │  Tools (exec, browser, fs…)    │
   └────────────────────────────────┘
        │
  Providers: Anthropic / OpenAI / Ollama / …
```

---

## API Surface (CRITICAL — read before any integration work)

The gateway at port `18789` exposes **two APIs**:

| API | Transport | Auth |
|-----|-----------|------|
| Control / Channels | WebSocket (proprietary protocol) | Bearer token via `connect` frame |
| **OpenAI-compatible REST** | HTTP | `Authorization: Bearer <token>` |

### REST endpoints (OpenAI-compatible)
```
GET  /v1/models
GET  /v1/models/{id}
POST /v1/chat/completions     ← use this for HA / any OpenAI client
POST /v1/embeddings
POST /v1/responses
```

Auth header: `Authorization: Bearer <token>`  
Token source: `gateway.auth.token` in `openclaw.json`  
Generate: `openclaw doctor --generate-gateway-token`

> **Unauthenticated requests return 404, not 401** (security through obscurity).  
> Always include the Bearer token; without it the endpoint appears not to exist.

---

## This Deployment: green-house

See `references/green-house.md` for full deployment details.

Key facts:
- Server: `green-house` (192.168.1.106 dynamic, local DNS resolves `green-house`)
- Process: `node openclaw/dist/index.js gateway --port 18789`
- LAN proxy: `http://green-house/api/openclaw/` → nginx → :18789
- HA bridge: `http://green-house/api/gateway/v1/` → jota-gateway → OpenClaw REST

---

## Reference Index

Load these when you need depth on a specific topic:

| File | When to load |
|------|-------------|
| `references/protocol.md` | Implementing WebSocket clients, debugging connections |
| `references/config-schema.md` | Editing `openclaw.json`, adding channels/models/tools |
| `references/skills-system.md` | Creating or debugging skills |
| `references/tools.md` | Tool configuration, groups, permissions |
| `references/channels.md` | Setting up Telegram, WhatsApp, Discord, etc. |
| `references/green-house.md` | This specific server deployment |
| `references/ha-bridge.md` | Home Assistant integration via jota-gateway |

---

## Common CLI Commands

```bash
openclaw gateway start|stop|restart|status
openclaw dashboard                    # open Control UI
openclaw skills list
openclaw skills install <name>        # from ClawHub
openclaw agent --message "..."
openclaw doctor                       # diagnose config issues
openclaw doctor --generate-gateway-token
```

---

## Troubleshooting Quick Reference

| Problem | Fix |
|---------|-----|
| `/v1/chat/completions` returns 404 | Missing `Authorization: Bearer <token>` header |
| Skill not loading | `openclaw skills list`, check YAML frontmatter, run `/new` |
| Config changes ignored | `openclaw gateway restart` |
| Auth challenge failing | Check token in `openclaw.json` → `gateway.auth.token` |
| Gateway won't start | `openclaw gateway status`, check port 18789 not in use |
