# OpenClaw wire protocol — living reference for jota-gateway

OpenClaw is beta software; its WebSocket API drifts between minor server versions with **no
changelog surfaced anywhere jota-gateway can see**. This file is the project's own,
continuously-updated technical record of what the protocol actually does, as observed against
the real running instance — not what any general OpenClaw documentation says it should do.

**Update this file whenever you discover a protocol change, inconsistency, or undocumented
event shape while working on jota-gateway** — dated, with the server version if known. This is
separate from the general-purpose `openclaw` skill (`~/.claude/skills/openclaw/`), which
explains what OpenClaw is and how to use it across any project; this file tracks *our specific,
current, hyper-detailed understanding* of its API as it affects this codebase. See also
`CLAUDE.md`'s "OpenClaw package" section for how jota-gateway's code responds to what's
documented here.

---

## Breaking change — v2026.6.11

Confirmed live against the real `green-house` OpenClaw instance on 2026-07-03 (PRs #70, #71).
Two changes, both **silent** — no error is raised, the old client code just quietly stops
working:

1. **`hello-ok`'s `snapshot` no longer embeds the agent roster.** A captured real `hello-ok`
   payload's `snapshot` only contains `presence`, `health`, `stateVersion`, `uptimeMs`,
   `sessionDefaults` — no `agents` key at all. See "Discovering Available Agents" below.
   `GatewayInfo.has_agent()` (`src/services/openclaw/models.py`) silently rejected every
   agent name until fixed.
2. **`chat.send` never gets a second `res`.** The completion signal is a `chat` event with
   `payload.state == "final"` — not a matching `res` frame. `stream_response()`
   (`src/services/openclaw/client.py`) hung indefinitely after the first token until fixed.

**If turns silently hang after the first token, or agent-name validation always rejects
everything that used to work** — check `hello-ok.server.version` against what's assumed here
before assuming your own code is at fault.

---

## hello-ok response (real shape, server 2026.6.11)

```json
{
  "type": "res", "id": "...", "ok": true,
  "payload": {
    "type": "hello-ok",
    "protocol": 4,
    "server": {"version": "2026.6.11", "connId": "..."},
    "features": {"methods": ["chat.send", "..."], "events": ["chat", "..."]},
    "snapshot": {
      "presence": {},
      "health": {},
      "stateVersion": 1,
      "uptimeMs": 12345,
      "sessionDefaults": {
        "defaultAgentId": "main",
        "mainKey": "main",
        "mainSessionKey": "agent:main:main",
        "scope": "per-sender"
      }
    },
    "pluginSurfaceUrls": {},
    "auth": {"deviceToken": "...", "role": "operator", "scopes": ["operator.read", "operator.write"]},
    "policy": {"maxPayload": 26214400, "maxBufferedBytes": 52428800, "tickIntervalMs": 30000}
  }
}
```

`snapshot.sessionDefaults.defaultAgentId` is the only agent info here — enough for the
*default* agent, not the full roster.

## Discovering Available Agents

Call `agents.list` right after `hello-ok` (`OpenClawClient.connect()` does this):

```json
// req
{"type": "req", "id": "...", "method": "agents.list", "params": {}}

// res
{
  "type": "res", "id": "...", "ok": true,
  "payload": {
    "defaultId": "main",
    "mainKey": "main",
    "scope": "per-sender",
    "agents": [
      {
        "id": "main",
        "name": "Main Agent",
        "identity": {"name": "Jota", "theme": "...", "emoji": "🦞"},
        "workspace": "/home/user/.openclaw/workspace",
        "agentRuntime": {"id": "auto", "source": "implicit"},
        "thinkingLevels": [{"id": "off", "label": "off"}, "..."],
        "thinkingOptions": ["off", "minimal", "low", "medium", "high"],
        "thinkingDefault": "high",
        "model": {"primary": "...", "fallbacks": ["..."]}
      }
    ]
  }
}
```

Note the field is `id`, not `agentId` (the older `hello-ok.snapshot.agents[]` shape — now gone
— used `agentId` + per-item `isDefault`; `agents.list`'s shape uses `id` + a top-level
`defaultId` to compare against instead). `GatewayInfo.update_agents_from_list()`
(`src/services/openclaw/models.py`) parses this.

**No per-agent `tools` field is included.** There is currently no known API method that
returns an agent's *resolved* tool list. `config.get` with no params returns the entire raw
`openclaw.json` (requires `operator.admin` scope, which jota-gateway's token does not
currently request), including `agents.list[].tools.allow`/`.deny` — but that's the config-level
allow/deny, not the resolved set after profile expansion. The only reliable way found so far:
ask the agent to do something and watch for `session.tool` events (below).

---

## Turn completion (`chat` events with `state`)

While the agent is responding, `chat` events stream:

```json
{
  "type": "event", "event": "chat",
  "payload": {
    "sessionKey": "my-session-key",
    "deltaText": "The answer is",
    "message": "The answer is",
    "replace": false,
    "state": "delta",
    "seq": 10
  }
}
```

`state: "final"` on the last chunk is the real completion signal (server 2026.6.11+ — see
breaking change above). `stream_response()` yields `status: "done"` on `state == "final"`; the
old `res`-based completion path is kept only as a fallback for older server versions.

---

## Tool Use (`session.tool` events)

Reverse-engineered from real traffic while building tool-call surfacing (PR #70) — not
documented anywhere upstream as of 2026-07-03.

```json
{
  "type": "event", "event": "session.tool",
  "payload": {
    "sessionKey": "my-session-key", "runId": "...",
    "data": {
      "phase": "start", "name": "read", "toolCallId": "call_function_8roff6zqcgdv_1",
      "args": {"path": "/home/user/.openclaw/workspace-foo/IDENTITY.md"}
    }
  }
}
// ... time passes while the tool executes ...
{
  "type": "event", "event": "session.tool",
  "payload": {
    "sessionKey": "my-session-key", "runId": "...",
    "data": {
      "phase": "result", "name": "read", "toolCallId": "call_function_8roff6zqcgdv_1",
      "isError": false,
      "result": {"content": [{"type": "text", "text": "<file contents>"}]}
    }
  }
}
```

| `data` field | Present in | Meaning |
|---|---|---|
| `phase` | both | `"start"`, `"update"` (streaming partial — observed, not reverse-engineered in detail, currently dropped), or `"result"` |
| `name` | both | Tool name (e.g. `read`, `exec`) |
| `toolCallId` | both | Correlates a `"start"` with its `"result"` — format `call_function_<random>_<n>`, treat as opaque |
| `args` | `"start"` | Arguments passed to the tool |
| `result` | `"result"` | `{"content": [{"type": "text", "text": "..."}, ...]}` — a list of typed content blocks, not a bare string. `ToolCallEvent.from_session_tool_payload()` (`src/services/openclaw/models.py`) flattens the `text`-type blocks and joins with `\n` |
| `isError` | `"result"` | Whether the tool call failed |

Routed by `FrameDispatcher._handle_session_tool()` to the active turn's queue, or to
`bridge.deliver_push_tool_call()` if there's no active client-initiated turn for that session.
Forwarded to the client as `{"type": "tool_call", ...}` only when `ClientConfig.tool_calls_enabled`
is `True` (default `False`).

**Verified live 2026-07-03** against the `ci-tester` test agent (see `tests/e2e/test_tool_use.py`):
asking it to read `IDENTITY.md` (a real file it has access to via the `read` tool) produced the
exact `start` → `result` sequence above, with the file's real content in `result`.

---

## Multiple turns per message (agent-initiated push during an active turn)

**Confirmed live 2026-07-03**, discovered while stabilizing `tests/e2e/` (`send_turn()` was
truncating responses intermittently before this was understood): a single `chat.send` can
produce **more than one** `turn_start`/`turn_end` pair on jota-gateway's client-facing WS, not
exactly one.

Observed directly: sending one message to an agent that uses a tool produced *three* distinct
turn-start events client-side — the tool-invoking turn, then at least one more — all tied to
the same incoming user message. This appears to be the agent treating each step of its own
multi-step reasoning/tool-use as a fresh "turn" from the gateway's perspective (an `agent`
event with `phase: "start"`/`"end"`, the same mechanism used for genuinely unsolicited
agent-initiated pushes — see `CLAUDE.md`'s "Agent-initiated push" bullet), firing **while the
original `chat.send`-triggered turn is still active**, not only as a fully separate later
event.

**jota-gateway mitigation (since fix for issue #84, 2026-07-10)**: `JotaBridge` collapses
multiple agent start/end pairs into a single client-facing turn. The first `agent` start opens
the logical turn (increments `_turn_seq` once, emits `turn_start`); subsequent `agent` starts
received while a push turn is already open are dropped silently. The first `agent` end closes
the logical turn (emits `turn_end`); subsequent `agent` ends received with no open turn are
also dropped. Result: any client that previously had to track "which `turn_id` is mine" across
multiple pairs now sees exactly **one** `turn_start`/`turn_end` pair per user message even when
the agent does tool-use or multi-step reasoning. The trade-off: the client cannot distinguish
intermediate reasoning steps from a single response (which it never needed to anyway).

**jota-gateway mitigation extended (fix for issue #112, 2026-08-04)**: the #84 mitigation above
only collapsed *nested* agent pairs into one push turn — it never asked whether a **normal**
`chat.send` turn was already active for that `session_key`. `FrameDispatcher._handle_agent_lifecycle`
was the one event handler (of three: `_handle_chat`, `_handle_session_tool`,
`_handle_agent_lifecycle`) that didn't check `TurnRegistry.get_queue_by_session()` first, so an
`agent` start/end pair could open a *second*, fully independent client-facing turn on top of a
normal one already in flight — reproduced live 2026-08-02 with the simplest possible prompt
("Responde solo con la palabra: hola"): two `turn_start` events, and the `token`/`turn_end`
frames arrived with mismatched `turn_id`s. Fixed by adding the same `get_queue_by_session()`
guard the other two handlers already had: while a normal turn's queue is registered for a
`session_key`, `agent` lifecycle events are dropped entirely (no push turn opens or closes) —
that turn's `chat`/`session.tool` content keeps reaching the client normally, only the
duplicate lifecycle framing is suppressed.

**Consequence for any code that consumes jota-gateway's client WS protocol** (and for anything
reading OpenClaw's own frames directly): do not assume "one message in → one turn out." Track
frames by `turn_id`/`sessionKey`, not by "the next `turn_end` I see." `tests/e2e/ws_helpers.py`'s
`send_turn()` was fixed to track only the first `turn_id` observed and ignore frames from other
turns — a stray unrelated turn ending early was silently truncating collection of the real
turn's tokens mid-word (reproduced twice: an empty tool-use response, `"CHARLIE"` truncated to
`"CHARL"` in the concurrent-sessions test).

---

## No per-session/per-message system-prompt hook exists (as of npm `openclaw@2026.4.12`)

**Investigated 2026-07-18** while resolving issue #100 (`system_prompt_extra` silently dropped).
Source: the local `openclaw` npm package (`~/node_modules/openclaw`, version `2026.4.12` —
**older** than the `2026.6.11` server version referenced elsewhere in this file; not verified
live against a running instance, but these are long-stable core schema/session methods, not the
volatile turn-completion signal documented above). If behavior here ever contradicts a real
server response, trust the server and update this section.

**`chat.send` cannot carry a system prompt.** Its wire schema
(`ChatSendParamsSchema`, TypeBox, `additionalProperties: false`) accepts exactly: `sessionKey,
message, thinking, deliver, originatingChannel, originatingTo, originatingAccountId,
originatingThreadId, attachments, timeoutMs, systemInputProvenance, systemProvenanceReceipt,
idempotencyKey`. `systemInputProvenance`/`systemProvenanceReceipt` are about *tracking where an
input message originated* (subagent lineage, cross-session sourcing), not a prompt field. With
`additionalProperties: false`, any extra field a client sends is rejected outright by the
server — there is no soft/ignored extension point here.

**`chat.inject` exists but is the wrong tool for this.** `{sessionKey, message, label?}` →
writes the message into the session's persisted transcript via
`appendAssistantTranscriptMessage` (so it *does* become context for future turns, despite the
gateway protocol doc's "transcript-only chat event" phrasing) — but always as an **assistant**
role message, and it broadcasts a visible `chat` event (`state: "final"`) to every subscribed
UI/node client for that session. Using it to carry system instructions would make the model
appear to have said its own steering instructions, and would show up as a visible transcript
entry — wrong role semantics and wrong visibility for an invisible system-level prompt.

**`sessions.patch` does not support a system-prompt field either.** Confirmed directly against
its handler (`applySessionsPatchToStore`, `src/gateway/sessions-patch.ts` in the OpenClaw
source): the only patchable fields are `spawnedBy, spawnedWorkspaceDir, spawnDepth,
subagentRole, subagentControlScope, label, thinkingLevel, fastMode, verboseLevel, traceLevel,
reasoningLevel, responseUsage, elevatedLevel, execHost, execSecurity, execAsk, execNode, model,
sendPolicy, groupActivation`.

**The only real system-prompt mechanisms in OpenClaw, and why neither fits jota-gateway's
per-client model:**

1. `agents.defaults.systemPromptOverride` / `agents.list[].systemPromptOverride` — static,
   per-**agent** config (settable via `agents.update`), documented in OpenClaw's own changelog
   as being for "controlled prompt experiments." Every session sharing that `agent` — i.e.
   every jota-gateway client using the same `agent` name — would get the same override. Wrong
   granularity: jota-gateway's `system_prompt_extra` is per-**client**, not per-agent.
2. The `before_prompt_build` plugin hook (`docs/concepts/agent-loop.md` in the OpenClaw
   package) can inject `systemPrompt`/`prependSystemContext`/`appendSystemContext` dynamically
   per turn — but only from **inside an OpenClaw plugin running on the OpenClaw server itself**.
   Not reachable from a remote WS "backend" client like jota-gateway. Building this would mean
   writing and installing a custom OpenClaw plugin with its own way of learning each
   jota-gateway client's `system_prompt_extra` — a separate, cross-repo project, not a
   same-scope fix.

**Conclusion applied in issue #100:** with no wire-level hook available today, the field was
removed from jota-gateway entirely (schema, DB, admin API, CLI) rather than faked via
message-concatenation. Revisit if OpenClaw ever adds a `chat.send` field for this, or if a
dedicated OpenClaw plugin becomes worth building.

---

## Change log of this file

- **2026-07-18**: added "No per-session/per-message system-prompt hook exists" — researched
  while resolving issue #100. Confirmed against OpenClaw's own source (`chat.send`'s strict
  TypeBox schema, `chat.inject`'s assistant-role transcript-write behavior, `sessions.patch`'s
  full field list) that there is no wire-level way to set a per-client system prompt today.
- **2026-07-10**: added the issue #84 mitigation note — `JotaBridge` now collapses multiple
  OpenClaw agent start/end pairs into a single client-facing turn_start/turn_end. Previously
  the bridge was emitting one pair per agent event, causing jota-voice (and any other client)
  to log 2–4 duplicate `turn_start`/`turn_end` frames per user message during tool use.
- **2026-07-03**: created. Documents the v2026.6.11 breaking changes (agents.list, chat
  completion signal), the `session.tool` event shape, and the multi-turn-per-message nuance —
  all discovered live while implementing PRs #70, #71, and the `tests/e2e/` suite (#74).
