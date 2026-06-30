# OpenClaw — green-house Deployment

## Server Facts

| Item | Value |
|------|-------|
| Hostname | `green-house` |
| LAN IP | `192.168.1.106` (dynamic — local DNS resolves `green-house`) |
| External | `green-house.alfonsogarre.com` via Cloudflare Tunnel |
| SSH | `ssh.alfonsogarre.com` via Cloudflare Tunnel |
| User | `sito` (sudo requires password — no NOPASSWD) |
| OS | Ubuntu 24.04 LTS headless |

## OpenClaw Process

```bash
# Running as launchd/systemd user service
node ~/.nvm/versions/node/v22.22.2/lib/node_modules/openclaw/dist/index.js gateway --port 18789

# Status
openclaw gateway status

# Logs
journalctl --user -u openclaw-gateway -f    # if systemd
# or check ~/.openclaw/logs/
```

## Config Location

`~/.openclaw/openclaw.json`

Auth token: `gateway.auth.token` — required for all REST API calls.  
Get token: `openclaw doctor --generate-gateway-token`

## Network Exposure

| Path | Protocol | Accessible from |
|------|----------|-----------------|
| `127.0.0.1:18789` | WebSocket + HTTP REST | Loopback only (direct) |
| `http://green-house/api/openclaw/` | HTTP | LAN only (nginx proxy) |
| `http://green-house/api/gateway/v1/` | HTTP | LAN only (jota-gateway bridge) |
| `https://green-house.alfonsogarre.com/api/openclaw/` | HTTPS | External via Cloudflare |

## nginx Proxy

Config: `/etc/nginx/includes/api-locations.conf`  
Proxy: `location /api/openclaw/ { proxy_pass http://127.0.0.1:18789/; ... }`

To add nginx → OpenClaw calls need Bearer token, either:
- Add `proxy_set_header Authorization "Bearer <token>";` in nginx (not recommended — hardcoded)
- Use `trusted-proxy` mode in OpenClaw (recommended — loopback auto-approves)
- Let jota-gateway inject the token programmatically

## All Services on green-house

| Service | Port | Listen | Status |
|---------|------|--------|--------|
| nginx | 80, 443 | 0.0.0.0 | ✅ |
| OpenClaw gateway | 18789 | loopback | ✅ |
| jota-transcriber (STT) | 8003 | 0.0.0.0 | ✅ |
| jota_db_api | 8002 | 0.0.0.0 | ✅ |
| jota-gateway (BFF) | 8004 | 0.0.0.0 | ⚠️ needs OpenClaw bridge |
| jota-speaker (TTS) | 8005 | — | ❌ stopped |
| Ollama | 11434 | 0.0.0.0 | ✅ |
| jota-orchestrator | 8000 | — | ✅ |

## Cloudflare Tunnel Config

`/etc/cloudflared/config.yml`

```yaml
tunnel: 513ed5b6-fe3c-44ee-bbef-cb7b51fd29a7
ingress:
  - hostname: green-house.alfonsogarre.com
    service: https://127.0.0.1:443
    originRequest:
      noTLSVerify: true
  - hostname: ssh.alfonsogarre.com
    service: ssh://127.0.0.1:22
  - service: http_status:404
```

`j.alfonsogarre.com` — exists in CF DNS but has no tunnel ingress rule. Not used.

## SSL Certificate

Self-signed mkcert cert. SANs: `green-house.local`, `greenhouse.local`, `localhost`, `192.168.1.105`  
Note: cert has `.105` (old IP), current is `.106`. Use `green-house.local` for LAN HTTPS.

Regenerate if needed:
```bash
mkcert -cert-file /etc/nginx/certs/server.crt \
       -key-file  /etc/nginx/certs/server.key \
       green-house.local localhost 192.168.1.106 127.0.0.1
sudo systemctl reload nginx
```
