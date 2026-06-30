# OpenClaw — Tools Reference

## Built-in Tools

| Tool | Group | Description |
|------|-------|-------------|
| `exec` / `process` | `group:runtime` | Shell commands, background processes |
| `browser` | `group:ui` | Chromium control (navigate, click, screenshot) |
| `web_search` | `group:web` | Web search |
| `web_fetch` | `group:web` | Fetch and parse web pages |
| `x_search` | `group:web` | X/Twitter search |
| `read` | `group:fs` | Read files in workspace |
| `write` | `group:fs` | Write files |
| `edit` | `group:fs` | Edit files (targeted changes) |
| `apply_patch` | `group:fs` | Apply unified diffs |
| `message` | `group:messaging` | Send messages across all channels |
| `image` | `group:media` | Analyze images |
| `image_generate` | `group:media` | Generate images |
| `tts` | `group:media` | Text-to-speech |
| `cron` | `group:automation` | Manage scheduled jobs |
| `gateway` | `group:openclaw` | Inspect/patch/restart gateway |
| `subagents` | `group:runtime` | Sub-agent orchestration |
| `sessions_*` | `group:sessions` | Session management |
| `canvas` | `group:ui` | Drive node Canvas (macOS/iOS/Android) |
| `nodes` | `group:nodes` | Discover paired mobile/desktop nodes |

## Tool Groups

Use in `tools.allow` / `tools.deny`:

| Group | Contains |
|-------|----------|
| `group:runtime` | exec, process, subagents |
| `group:fs` | read, write, edit, apply_patch |
| `group:sessions` | sessions_* |
| `group:memory` | memory tools |
| `group:web` | web_search, web_fetch, x_search |
| `group:ui` | browser, canvas |
| `group:automation` | cron, hooks |
| `group:messaging` | message |
| `group:nodes` | nodes, node.invoke |
| `group:media` | image, image_generate, tts |
| `group:openclaw` | gateway, config |

## Profiles (shorthand)

| Profile | Includes |
|---------|----------|
| `full` | Everything |
| `coding` | group:fs, group:runtime, group:web, browser |
| `messaging` | group:messaging, group:sessions |
| `minimal` | read, message |

## Configuration

```jsonc
{
  "tools": {
    "profile": "coding",
    "allow": ["group:fs", "browser"],   // added on top of profile
    "deny":  ["exec"]                   // deny always wins
  }
}
```

## NEVER modify via `config.patch`:
- `tools.exec.ask`
- `tools.exec.security`

These are protected and can only be changed via direct config edit + restart.
