# OpenClaw wire protocol — living reference for fogar-gateway

OpenClaw is beta software; its WebSocket API drifts between minor server versions with **no
changelog surfaced anywhere fogar-gateway can see**. This file is the project's own,
continuously-updated technical record of what the protocol actually does, as observed against
the real running instance — not what any general OpenClaw documentation says it should do.

**Update this file whenever you discover a protocol change, inconsistency, or undocumented
event shape while working on fogar-gateway** — dated, with the server version if known. This is
separate from the general-purpose `openclaw` skill (`~/.claude/skills/openclaw/`), which
explains what OpenClaw is and how to use it across any project; this file tracks *our specific,
current, hyper-detailed understanding* of its API as it affects this codebase. The code that
responds to what's documented here lives in `src/fogar_gateway/services/openclaw/`.

**Minimum supported OpenClaw server: 2026.6.11** (since 2026-10-28, issue #262). The gateway no
longer understands the older protocol shapes described below as "gone".

---

## Breaking change — v2026.6.11

Confirmed live against the real `green-house` OpenClaw instance on 2026-07-03 (PRs #70, #71).
Two changes, both **silent** — no error is raised, the old client code just quietly stops
working:

1. **`hello-ok`'s `snapshot` no longer embeds the agent roster.** A captured real `hello-ok`
   payload's `snapshot` only contains `presence`, `health`, `stateVersion`, `uptimeMs`,
   `sessionDefaults` — no `agents` key at all. See "Discovering Available Agents" below.
   `GatewayInfo.has_agent()` (`src/fogar_gateway/services/openclaw/models.py`) silently rejected every
   agent name until fixed.
2. **`chat.send` never gets a second `res`.** The completion signal is a `chat` event with
   `payload.state == "final"` — not a matching `res` frame. `stream_response()`
   (`src/fogar_gateway/services/openclaw/client.py`) hung indefinitely after the first token until fixed.

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
        "identity": {"name": "Fogar", "theme": "...", "emoji": "🦞"},
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
(`src/fogar_gateway/services/openclaw/models.py`) parses this.

**No per-agent `tools` field is included.** There is currently no known API method that
returns an agent's *resolved* tool list. `config.get` with no params returns the entire raw
`openclaw.json` (requires `operator.admin` scope, which fogar-gateway's token does not
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
old `res`-based completion (`status: "done"`) was removed on 2026-10-28 (issue #262).

> Since 2026.10.5 `final` also carries the full reply text and no `deltaText` — see
> "Breaking change — OpenClaw 2026.10.5" below.

### Agent failure after the turn was accepted (2026-11-01, server 2026.10.5, issue #300)

When the agent fails after `chat.send` was accepted (`res ok`, `status: "started"`), for
example with no provider credentials, there is no `final` and no `agent` `phase: "end"`.
Observed on green-house (structure only):

1. `chat` `state: "status"` (`preparing_workspace`, `preparing_context`);
2. `agent` with `data.phase` `model`, then `fallback_step`, then **`error`** (`data` has
   `error`, `startedAt`, `endedAt`, `executionSettled`); no `start` phase was seen;
3. `chat` **`state: "error"`** with `errorMessage` (and `runId`, `seq`).

How the gateway reacts: a `chat` `state: "error"` ends a normal turn at once with
`orchestrator_error` and without `chat.abort` (the run is already over); `errorMessage` never
reaches the client or the logs. An `agent` `phase: "error"` closes a push turn the same way as
`end`. Before #300 the normal turn waited for `turn_timeout` (`TURN_TIMEOUT_S`, 120 s) and a
failed push stayed open.

---

## Breaking change — OpenClaw 2026.10.5 (2026-10-20, issue #241)

Captured live against production (2026.10.5) for a one-sentence prompt. Frame order
(`payload` fields only, text shortened):

```
chat {state:"status", phase:"preparing_workspace", seq:1}        # no deltaText
chat {state:"status", phase:"preparing_context",   seq:2}
chat {state:"status", phase:"starting_model",      seq:4}
chat {state:"delta",  seq:6, deltaText:"1, 2, 3,",
      message:{"role":"assistant","content":[{"type":"text","text":"1, 2, 3,"}],"timestamp":...}}
chat {state:"delta",  seq:9, deltaText:"", replace:true,
      message:{"role":"assistant","content":[{"type":"text","text":""}],...}}
chat {state:"final",  seq:11, stopReason:"stop",                  # NO deltaText
      message:{"role":"assistant","content":[{"type":"text","text":"1, 2, 3, 4, 5"}],...}}
```

What changed versus 2026.6.11/2026.7.1:

- `state:"final"` carries the **complete** reply in `message` (`content` is a list of typed
  parts; only `type:"text"` parts matter) and **no `deltaText`**. The deltas received before
  it can be a strict prefix of the reply (here `'1, 2, 3,'`), so a client that only reads
  `deltaText` loses the tail (`' 4, 5'`).
- `delta` events with `replace:true` may appear (empty `deltaText` in the capture). They are
  not a retraction the gateway can honor: tokens already sent to the client cannot be taken
  back. Official semantics below ("Official delta/replace/snapshot semantics", issue #243).
- New `chat` events with `state:"status"` and a `phase` (`preparing_workspace`,
  `preparing_context`, `starting_model`, ...). They carry no text and are ignored.

### Official delta/replace/snapshot semantics (issue #243)

Source: `docs/gateway/protocol/rpc-bootstrap-and-events.md` of the OpenClaw 2026.10.5
package (read on the server by the maintainer; summarized here):

- `chat` `state:"delta"`: `deltaText` is **what was added**. The **first** text frame of a run
  for a receiver (also when it joins mid-run or reconnects) additionally carries the complete
  `message`; later append frames **omit `message`**.
- A supplied `message` is **authoritative and already includes `deltaText`**: never add the
  delta twice.
- Non-prefix replacements carry `replace:true` and use `deltaText` as the **entire**
  replacement text (the empty string deletes it); replacements always include a full snapshot.
- Pending `chat` appends are concatenated in order and flushed **at most every 75 ms** per run
  (`LIVE_TEXT_PACING_MS = 75`, `src/gateway/server-chat.ts:125` in OpenClaw v2026.10.5); tool
  and terminal boundaries flush pending text before settlement.
- The "at most once every two seconds" limit of that same document (`NARRATION_INTERVAL_MS =
  2_000`, `src/gateway/server-broadcast-narration.ts`) applies **only** to `session.narration`
  snapshots for subscriptions declared with `mode:"narration"`, not to `chat` deltas. The
  gateway does not use narration mode: it only sends `sessions.subscribe`
  (`src/fogar_gateway/services/openclaw/frames.py:28-29`).
- `final`, `aborted` and `error` keep full-message semantics.

**Deliberate gateway decision:** the gateway emits a text tail only for `final`. On
`aborted` and `error` it does **not** emit any tail from `message`: after an `aborted` the
client has already cut the reply (barge-in or cancel), and speaking more text after the user
interrupted would be wrong; an `error` may omit `message` on purpose. Pinned by a test.

How the gateway responds (`src/fogar_gateway/services/openclaw/chat_text.py`, used by
`OpenClawClient.stream_response()` and `FogarBridge.deliver_push()`): `ChatTextTracker` keeps
`current` (the message text per OpenClaw) and `emitted` (already delivered). `replace:true` →
`current = deltaText`; `message` present → `current` = its text (no re-adding `deltaText`);
otherwise `current += deltaText`; `final` → `current` = text of the final `message`. After
each update it emits only `missing_tail(emitted, current)`. In order: current starts with what
was delivered (including nothing delivered) → the suffix; equal → nothing; equal after
stripping leading whitespace on both sides → the suffix of the stripped text; a strict,
non-empty prefix of what was delivered (retraction) → nothing, `emitted` is kept (so regrowth
does not repeat text) and a fixed INFO line without content is logged; otherwise (real
divergence, e.g. a non-prefix `replace`, a correction in `final`, or `final.message` holding
only the last of several assistant messages) → the **full** current text plus a WARNING with a
fixed reason (no content): losing the reply is worse than repeating a fragment. An empty
`current` (empty replace or empty snapshot) emits nothing and does not reset `emitted`. The
tracker resets after every `final` (and per turn / on opening a push turn). Tokens already sent
cannot be retracted, so a retraction to a shorter text is not reflected to the client (and
never re-emitted).
The tracker also keeps `all_out`, the concatenation of every token delivered in the response;
when `current` diverges from the last aligned text it is compared against `all_out` before
falling back to the divergence policy (this avoids re-emitting the whole `final` after an
empty replace followed by a `delta` without `message`). A whitespace-only `emitted` never
logs the divergence WARNING.
Observation: with 2026.10.5 most of the text arrives at once in `final` (capture: a
16-character delta, a 409-character final), so TTS starts only when generation ends. The 2 s
narration interval does not explain it (it does not apply to `chat`, see above); fixing that
latency is an OpenClaw streaming-configuration matter.

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
| `result` | `"result"` | `{"content": [{"type": "text", "text": "..."}, ...]}` — a list of typed content blocks, not a bare string. `ToolCallEvent.from_session_tool_payload()` (`src/fogar_gateway/services/openclaw/models.py`) flattens the `text`-type blocks and joins with `\n` |
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
produce **more than one** `turn_start`/`turn_end` pair on fogar-gateway's client-facing WS, not
exactly one.

Observed directly: sending one message to an agent that uses a tool produced *three* distinct
turn-start events client-side — the tool-invoking turn, then at least one more — all tied to
the same incoming user message. This appears to be the agent treating each step of its own
multi-step reasoning/tool-use as a fresh "turn" from the gateway's perspective (an `agent`
event with `phase: "start"`/`"end"`, the same mechanism used for genuinely unsolicited
agent-initiated pushes — see `docs/client-protocol.md` §10), firing **while the
original `chat.send`-triggered turn is still active**, not only as a fully separate later
event.

**fogar-gateway mitigation (since fix for issue #84, 2026-07-10)**: `FogarBridge` collapses
multiple agent start/end pairs into a single client-facing turn. The first `agent` start opens
the logical turn (increments `_turn_seq` once, emits `turn_start`); subsequent `agent` starts
received while a push turn is already open are dropped silently. The first `agent` end closes
the logical turn (emits `turn_end`); subsequent `agent` ends received with no open turn are
also dropped. Result: any client that previously had to track "which `turn_id` is mine" across
multiple pairs now sees exactly **one** `turn_start`/`turn_end` pair per user message even when
the agent does tool-use or multi-step reasoning. The trade-off: the client cannot distinguish
intermediate reasoning steps from a single response (which it never needed to anyway).

**fogar-gateway mitigation extended (fix for issue #112, 2026-08-04)**: the #84 mitigation above
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

**Consequence for any code that consumes fogar-gateway's client WS protocol** (and for anything
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

**The only real system-prompt mechanisms in OpenClaw, and why neither fits fogar-gateway's
per-client model:**

1. `agents.defaults.systemPromptOverride` / `agents.list[].systemPromptOverride` — static,
   per-**agent** config (settable via `agents.update`), documented in OpenClaw's own changelog
   as being for "controlled prompt experiments." Every session sharing that `agent` — i.e.
   every fogar-gateway client using the same `agent` name — would get the same override. Wrong
   granularity: fogar-gateway's `system_prompt_extra` is per-**client**, not per-agent.
2. The `before_prompt_build` plugin hook (`docs/concepts/agent-loop.md` in the OpenClaw
   package) can inject `systemPrompt`/`prependSystemContext`/`appendSystemContext` dynamically
   per turn — but only from **inside an OpenClaw plugin running on the OpenClaw server itself**.
   Not reachable from a remote WS "backend" client like fogar-gateway. Building this would mean
   writing and installing a custom OpenClaw plugin with its own way of learning each
   fogar-gateway client's `system_prompt_extra` — a separate, cross-repo project, not a
   same-scope fix.

**Conclusion applied in issue #100:** with no wire-level hook available today, the field was
removed from fogar-gateway entirely (schema, DB, admin API, CLI) rather than faked via
message-concatenation. Revisit if OpenClaw ever adds a `chat.send` field for this, or if a
dedicated OpenClaw plugin becomes worth building.

---

## Session key carries the per-client identity (fogar-gateway convention)

Every turn fogar-gateway sends over `chat.send` uses `sessionKey = "agent:{agent}:{client.id}"`,
where `client.id` is the `ClientRecord` UUID of the authenticated caller (WS handshake or REST
`/v1/*` with a Bearer key), not a shared global key. This is a fogar-gateway convention layered on
OpenClaw's opaque `sessionKey` string, not something the OpenClaw server enforces or validates.
Consequences observable on the wire:

- Two clients talking to the same agent get independent OpenClaw sessions (separate history).
- Events come back tagged with that same `sessionKey`; the gateway recovers the client with
  `sk.rsplit(":", 1)[-1]` (safe for agent names or keys containing extra colons).
- The REST trusted-origin legacy path (no Bearer) has no client row, so it uses the fixed id `ha`.
- Since server 2026.11.1, `chat.send` rejects session keys that do not identify an agent; ours
  always do (see "Connect deadline and `chat.send` session keys" below).

How the `agent` segment is resolved and validated lives in `src/fogar_gateway/core/agent_policy.py`
(summarised for clients in `docs/client-protocol.md` §1, "Selección de agente").

---

## Frame size, malformed frames and connection end (2026-10-18, issue #213)

Observed/decided while hardening the gateway's boundary with OpenClaw (audit G-03/G-04/G-05/G-47/G-48):

- **`hello-ok.policy.maxPayload` (25 MiB = 26214400 on 2026.6.11) is the largest frame the
  server may send.** `websockets` defaults to a 1 MiB inbound limit, so one larger frame closes
  the socket with 1009 and drops every multiplexed session. The client connects *before*
  `hello-ok`, so it starts with 25 MiB (or the last announced value on a reconnect) and re-aligns
  the live socket right after `hello-ok`; clamped to [1 MiB, 64 MiB].
- **Malformed frames are possible in principle** (not valid JSON, a JSON array, `payload: null`,
  `data: null`). The gateway drops them per frame; they never end the connection.
- **A clean close (1000) from the server ends the iteration silently** (no exception), so
  in-flight turns must be failed explicitly on any exit from the listen loop.
- **`res` frames:** `chat.send`'s ack is `ok:true` with `payload.status == "started"`. Only
  `ok:false` is a failure that ends the turn. Any other `ok:true` status (including the
  pre-2026.6.11 `status: "done"`, no longer supported) is not a turn end; the real completion
  is the `chat` event with `state == "final"`.
- **`hello-ok` carries `protocol` and `server.version`;** the gateway logs both once per
  connection (INFO) so version drift shows in the logs.

---

## Errors toward clients and handshake failures (2026-10-19, issue #228)

OpenClaw's own error text (`res` with `ok:false`, a `hello` rejection, an unexpected
handshake frame) and raw exception messages (websockets close reasons end up inside
`ConnectionClosed`'s text) are internal: the gateway never relays them to a client nor writes
them to a log. What it does instead:

| Situation | Client sees (`TURN_ERROR.message` / `/v1` error) | Log |
|---|---|---|
| `chat.send` can't be written | `orchestrator_send_failed` | WARNING with the exception *type* only |
| `res` with `ok:false` for a turn | `orchestrator_error` | WARNING with OpenClaw's short `error.code` only (see below) |
| Not connected / reconnecting | `orchestrator_unavailable` | — |
| Connection ends mid-turn | `orchestrator_disconnected` (#213) | type / close codes only |
| `turn_timeout`, `turn_overflow`, `turn_in_progress` | same literals (unchanged) | — |
| Unexpected exception wrapping the stream | `orchestrator_error` | type only; traceback without message at DEBUG |

Handshake (`connect()`): a rejected `hello`, a first frame that is not `connect.challenge`, or
a challenge/`hello`/`hello.payload` that is not a JSON object all raise
`OrchestratorHandshakeError`, whose reason is the fixed `orchestrator_handshake_failed`. It is
retried by the reconnect loop like any failed connect (the one exception, the retryable
`startup-sidecars` `UNAVAILABLE`, is first retried inside `connect()` itself — see "OpenClaw
still starting" below). `ReconnectingOpenClawClient`'s
`last_error` (exposed by `GET /admin/orchestrator/status`) holds that code, or just the
exception type name (`OSError`, `TimeoutError`...) for anything else. The same `last_error`
rule applies to every wrapper that goes through `reconnect_with_backoff`.

For operator diagnostics, a rejected `hello`, a failed `agents.list` and a rejected turn log one
WARNING line with OpenClaw's `error.code` (`code=...`), only when it matches
`[A-Za-z0-9_.:-]{1,48}`; otherwise `code=unspecified`. `error.message` and the rest of the body
are never read (the one exception: a rejected `connect`'s `retryable`, `retryAfterMs` and
`details.reason`, read only to recognise `startup-sidecars` and compute the wait; only that
computed wait is logged, never the raw error).

Note: the `websockets` library itself logs raw frames at DEBUG on its own `websockets.*`
loggers; keep those at their default level in production.

## `runId`, `chat.abort` and a concurrent `chat.send` (2026-10-22/23, server 2026.10.5, issue #219)

Observed live on green-house with throwaway session keys; structure only (no text recorded).
Full notes in the team's Obsidian vault (observations of 2026-10 on OpenClaw and the slow client on Linux).

- **Every `chat` and `agent` event carries `runId`** (and `seq`), including `chat` `aborted`.
  The `chat.send` ack arrives in 30–60 ms as `res ok` with `payload {runId, status}`, so the
  run id is known before any event of the run.
- **Non-terminal `chat` states exist:** `state:"status"` with `phase`
  (`preparing_workspace`, `preparing_context`, `starting_model`). Terminal states seen:
  `final` (with `stopReason`) and `aborted`; `error` was not observed here (it was later, on
  2026-11-01: see "Agent failure after the turn was accepted"). Do **not** treat
  "anything but `delta`" as terminal.
- **`chat.abort`:** `chat` `state:"aborted"` (+ `stopReason`, and `message` if text had
  started) arrives 9–28 ms after the abort, then `agent` `lifecycle end` and the
  `res ok` `payload {ok, aborted, runIds}`. **No late `delta`/`final`** in the next 10 s; only
  `sessions.changed`/`session.message` bookkeeping. Same if the abort lands before the model
  starts.
- **`chat.send` while another run is active on the same `sessionKey`:** accepted (`res ok`,
  new `runId`), no error. The new run gets **no lifecycle/status/delta events of its own**;
  it ends with a `chat` `final` carrying its `runId` while the original run keeps streaming
  and later sends its own `final` (OpenClaw appears to fold the message into the running run;
  not verified, no text recorded).
- Events the gateway ignores today: `agent` streams `run_status`, `assistant`, `thinking`,
  `usage`; `sessions.changed`, `session.message`.

**Breaking for fogar-gateway as of v1.21.0:** `stream_response()` ends a turn on the first
`chat` `final` of its `sessionKey` and its queue receives every `chat` event of that key,
regardless of `runId`. With a push in flight, a user `send` therefore receives the push's
deltas and is closed by whichever `final` comes first, and the rest of the other run is
lost. Fix belongs to M9 (correlate by `runId`; issue #202).

---

## OpenClaw still starting: `UNAVAILABLE` / `startup-sidecars` on `connect` (2026-11-06, server 2026.11.1)

Read from the npm package `openclaw@2026.11.1` (not captured live). The `connect.challenge`
arrives before the server has finished booting; while its startup sidecars are pending, the
answer to `connect` is a retryable rejection instead of `hello-ok`, and then the server closes
the socket with **1013** `"gateway starting"`:

```json
{
  "type": "res", "id": "...", "ok": false,
  "error": {
    "code": "UNAVAILABLE",
    "message": "gateway starting; retry shortly",
    "retryable": true,
    "retryAfterMs": 500,
    "details": {"reason": "startup-sidecars"}
  }
}
```

- `reason` lives in `error.details.reason`; `retryAfterMs` and `retryable` are top-level fields
  of `error` (`ErrorShapeSchema`: `retryAfterMs` integer ≥ 0, `dist/sessions-CVESrViJ.mjs:2065`).
- Emitted by `rejectGatewayStartupConnect` (`dist/message-handler-D_4tBaZX.mjs:590-604`), with
  the constants in `dist/startup-unavailable-D0-EeFjq.mjs` (`GATEWAY_STARTUP_CLOSE_CODE = 1013`).
  Documented in `docs/gateway/protocol/handshake.md` ("Retry within your connection budget
  instead of treating it as a terminal handshake failure") and `docs/gateway/embedding.md`
  ("Wait for protocol readiness").
- OpenClaw's own test, `isRetryableGatewayStartupUnavailableError`, requires `code ==
  "UNAVAILABLE"`, `retryable === true` and `details.reason == "startup-sidecars"`; its reference
  client (`resolveGatewayStartupRetryAfterMs`) clamps the wait to 100–2000 ms, 500 ms when
  `retryAfterMs` is missing.

How the gateway reacts (`OpenClawClient._do_connect`): on exactly that error it waits
`retryAfterMs` (500 ms if missing or invalid, at least 100 ms, at most 5 s, and never longer
than what is left of a 30 s budget counted from the first attempt) and repeats the whole
handshake on a new socket. Once the budget is spent no new attempt starts: it raises
`OrchestratorStartingError`, a subclass of `OrchestratorHandshakeError` with the same stable code
(`orchestrator_handshake_failed`), and the reconnect loop takes over (`fogar-gw onboard` uses the
subclass to say "not answering in time" instead of "token rejected"). Any other `UNAVAILABLE`
(e.g. `details.reason: "authenticated-profile-unavailable"`) fails the handshake at once, as
before. Only the fixed text
and the computed wait are logged (the first retry of a `connect()` at INFO, the rest at DEBUG),
never `message` or `details`. Side effect: the lifespan's first `connect()` can hold the
gateway's startup (port 8004 still closed) for up to those 30 s while OpenClaw boots; that is why
`fogar-gw install|start|restart`, and `fogar-gw config set` when it restarts the service, wait up
to 45 s for `/healthz` (`HEALTH_TIMEOUT_S`).

The `connect` frame also carries `client.displayName: "fogar-gateway"` since this date. The
`client` object is closed (`additionalProperties: false`), but `displayName` is an optional
non-empty string in `ConnectParamsSchema` both in 2026.11.1 (`dist/sessions-CVESrViJ.mjs:1936`)
and in 2026.4.12 (`dist/protocol-9t-_D3R4.js`), so every supported server (≥ 2026.6.11) accepts it.

## Token rejected on `connect`, and `/healthz` (2026-11-06, server 2026.11.1)

Read in the npm package source, not observed live. A wrong or missing token is answered with
`ok:false` and then a close with **1008** (`dist/message-handler-D_4tBaZX.mjs`,
`sendHandshakeErrorResponse`):

```json
{"type": "res", "id": "...", "ok": false,
 "error": {"code": "INVALID_REQUEST", "message": "unauthorized: gateway token mismatch (...)",
           "details": {"code": "...", "authReason": "token_mismatch", "...": "..."}}}
```

`authReason` is also `token_missing`, `rate_limited` (with `retryable`/`retryAfterMs`), etc. The
gateway does not read it: a `type: "res"` frame with `ok: false` (other than `startup-sidecars`)
raises `OrchestratorRejectedError`, a subclass of `OrchestratorHandshakeError` with the same stable
code. Anything else in its place (an event such as `tick`, a `res` whose `ok` is missing, `null`
or another falsy value other than `false`, a malformed or non-object frame) raises the plain
`OrchestratorHandshakeError`.
`fogar-gw onboard` (`cli/verify.py`) reports only the subclass as "OpenClaw rejected the token",
and the plain error as an unexpected answer.

`GET /healthz` (and `/health`) answers 200 `{"ok":true,"status":"live"}` as soon as the HTTP
listener is up (`dist/gateway-prewarm.js`, `handleGatewayProbeRequest`). It carries **no server
version**: `detect_openclaw()` returns `""` (found, version unknown); the version only arrives in
`hello-ok`, after a successful token handshake.

## Connect deadline and `chat.send` session keys (2026-11-06, server 2026.11.1)

Read in the package source of 2026.11.1 and compared with 2026.10.5, not observed live:

- **Handshake deadline 15 s → 30 s.** `DEFAULT_PREAUTH_HANDSHAKE_TIMEOUT_MS` went from `15e3` to
  `3e4` (`dist/timeouts-ClVlVfGz.mjs`). Per `docs/gateway/protocol/handshake.md` it is a *total*
  deadline for challenge → `connect` → `hello-ok`, not an idle timeout, shared by the server's
  pre-auth timer and OpenClaw's own client; operators can change it with
  `OPENCLAW_HANDSHAKE_TIMEOUT_MS`. The gateway's own waits (15 s for `connect.challenge`, then
  30 s for `hello-ok`, in `OpenClawClient._connect_once`) are not shorter than that default, so a
  slow host is not cut short by the gateway; a server that closes at its deadline fails that
  attempt like any closed socket and the reconnect loop takes over.
- **`chat.send` rejects ambiguous session keys** with `INVALID_REQUEST`: malformed keys, an
  `agentId` that conflicts with an agent-qualified key, and unqualified keys that do not identify
  an agent (`docs/gateway/protocol/rpc-session-control.md:14`, absent in 2026.10.5). The gateway is
  not affected: every `sessionKey` it sends is agent-qualified (`agent:{agent}:{client.id}`, see
  "Session key carries the per-client identity") and `frames.chat_send` never sends `agentId`.

## Device identity, pairing and `operator.admin` (2026-11-13, server 2026.10.5)

Read in the npm package `openclaw@2026.10.5` (`dist/message-handler-*.mjs` ~1135–1290 and ~3081,
`dist/node-connect-reconcile-*.mjs`, `dist/device-auth-*.mjs`, `dist/ed25519-signature-*.mjs`,
`dist/connect-error-details-*.mjs`, `dist/core-method-policy-*.mjs`, `docs/gateway/protocol/auth.md`)
and in the reference client `@openclaw/gateway-client@2026.8.1`; the local admin path was also
**probed live** against a 2026.10.5 instance on loopback. Remote pairing was **not** observed live
(no non-loopback port available where it was probed): it is read from the source.

**Who the gateway is.** `connect` declares `client.id: "gateway-client"`, `client.mode: "backend"`,
`role: "operator"`. That id/mode is also the default of OpenClaw's own reference client for external
apps (`@openclaw/gateway-client`, only in its code, not its docs), so it stays. Since this date the
gateway asks for `scopes: ["operator.read", "operator.write", "operator.admin"]` (no feature calls
an admin method yet) and signs every `connect` with a device identity.

**Local trust path (same host).** With `gateway-client`/`backend` on a loopback connection
authenticated by the shared token (`resolvePairingLocality` → `direct_local` or
`shared_secret_loopback_local`, no browser `Origin`, no proxy headers),
`shouldSkipLocalBackendSelfPairing` is true: OpenClaw trusts it directly, **creates no pairing
record, issues no device token and keeps the requested scopes**. Live probe: asking for read +
write + admin → `hello-ok.auth.scopes` has all three and `sessions.reset`/`sessions.compact` answer
`ok`; without admin they answer `FORBIDDEN: missing scope: operator.admin`. Same-host LAN or
tailnet addresses, and OpenClaw in Docker behind a published port (the connection arrives from the
bridge address), count as **remote**.

**Remote: device identity and pairing.** From a non-loopback address a device identity is
required; without it the scopes are cleared (`missing scope` on every call) or the connect fails
with `NOT_PAIRED` / `DEVICE_IDENTITY_REQUIRED`. With it, the **shared token is checked first** (a
wrong one is `INVALID_REQUEST` / `AUTH_TOKEN_MISMATCH`, never a pairing request), then an unpaired
device gets:

```json
{"type": "res", "ok": false,
 "error": {"code": "NOT_PAIRED", "message": "pairing required: device is not approved yet",
           "details": {"code": "PAIRING_REQUIRED", "reason": "not-paired", "requestId": "...",
                       "remediationHint": "...", "deviceId": "<hex>", "requestedRole": "operator",
                       "requestedScopes": ["..."]}}}
```

and a close 1008. For an operator backend there is no `recommendedNextStep` (it is
`wait_then_retry` only for bootstrap/Control UI/node waits). The operator approves once on the
OpenClaw host with `openclaw devices approve <requestId>` (without an id, or with `--latest`, it
only previews the newest request and prints the exact command). The next `connect` answers
`hello-ok` with `auth: {method, role, scopes, deviceToken, issuedAtMs}`; a device that is already
paired gets its token even on the local path. `reason` can also be `role-upgrade`,
`scope-upgrade` or `metadata-upgrade` (e.g. a different `client.platform` than when it was paired).

**Device auth v3.** Identity: an Ed25519 key. `device.id` = SHA-256 hex of the raw 32-byte public
key (`deriveDeviceIdFromPublicKey`); `device.publicKey` = the raw key in base64url without padding
(PEM is also accepted); `device.signature` = Ed25519 over the UTF-8 payload, base64url without
padding; `device.signedAt` = `connect.challenge.payload.ts` (must be within ±120 s of the server
clock); `device.nonce` = the challenge nonce. The payload (`buildDeviceAuthPayloadV3`; `v2` without
the last two fields is still accepted):

```
v3|{deviceId}|{client.id}|{client.mode}|{role}|{scopes joined by ","}|{signedAt}|{token}|{nonce}|{platform}|{deviceFamily}
```

`token` is `auth.token ?? auth.deviceToken ?? auth.bootstrapToken ?? ""`; `platform` and
`deviceFamily` are trimmed and lower-cased (ASCII A–Z only), `""` when absent. A bad proof is
`INVALID_REQUEST` with `details.code` `DEVICE_AUTH_DEVICE_ID_MISMATCH`, `…_SIGNATURE_EXPIRED`,
`…_NONCE_REQUIRED`, `…_NONCE_MISMATCH`, `…_SIGNATURE_INVALID` or `…_PUBLIC_KEY_INVALID`.

**Device token.** The reference client sends a stored device token in **both** `auth.token` and
`auth.deviceToken`, requests the scopes stored with it, and in `acceptHello` stores
`hello-ok.auth.deviceToken` with `hello-ok.auth.scopes ?? []` when it is new or different; an
identical token always keeps its stored scopes (`stored.token === token ? stored.scopes : …`).
A token that no longer works (revoked, rotated, device removed) is `AUTH_DEVICE_TOKEN_MISMATCH`;
one that does not cover the requested scopes is `AUTH_SCOPE_MISMATCH`. `hello-ok.auth.scopes` is
the live session's scopes (what was requested, within the grant); older servers (≥ 2026.6.11)
may omit it.

**How the gateway reacts** (`services/openclaw/device.py`, `client.py`): the identity lives in
`<base>/data/openclaw-device.json` (0600; private key, public key, deviceId and, once paired, the
device token with its scopes and the OpenClaw `host:port` it belongs to). Every write is atomic
(temp 0600 + fsync + rename) and every read-modify-write runs under an exclusive `flock` on the
sibling `.openclaw-device.json.lock`, because `fogar-gw onboard` can write it while the gateway
runs; file I/O runs in `asyncio.to_thread`, with one read per connect (identity + token).
`fogar-gw migrate --from-docker` copies it from `DIR/data/` when present (read-only source, 0600,
never over an existing one). Every challenge with a nonce is signed. A stored token for the
configured `OPENCLAW_HOST:OPENCLAW_PORT` is preferred over `OPENCLAW_TOKEN`; on
`AUTH_DEVICE_TOKEN_MISMATCH`/`AUTH_SCOPE_MISMATCH` the gateway drops it and retries once, on a new
socket, with the shared token. **`fogar-gw onboard` never uses the stored device token**
(`OpenClawClient(use_device_token=False)`): it tests the token that was pasted, still signing so
pairing is detected.

- **Same token, different scopes (deliberate deviation).** Like the reference client an identical
  token never narrows its stored scopes, but when `hello-ok.auth.scopes` is a strict superset of
  them (an approved scope upgrade can keep the token: `ensureDeviceToken` reuses it when it
  already covers the request) the broader set is stored, so the next `connect` requests it.
  Without `hello-ok.auth.scopes` the scopes are **unknown**: `None` in `GatewayInfo`/
  `OrchestratorAccess`, `null` in the file (never an empty list), «desconocidos» in the CLI.
- **`PAIRING_REQUIRED`** to a connect that carried the device proof raises
  `OrchestratorPairingRequired` (stable code `orchestrator_pairing_required`, in `last_error`;
  `auth_method` says which credential OpenClaw accepted), logs the request id and the approval
  command once per request at WARNING, and the usual reconnect loop keeps retrying until the
  approval. `PAIRING_REQUIRED` or `DEVICE_IDENTITY_REQUIRED` to a connect **without** device proof
  (identity file unusable, or a challenge without nonce) raises
  `OrchestratorDeviceIdentityRequired` (`orchestrator_device_identity_required`, `cause`
  `identity_file_invalid`|`challenge_without_nonce`) and never suggests `devices approve`.
- `/admin/state` (`services.orchestrator`), `fogar-gw status` and `fogar-gw doctor` show the
  granted scopes, `admin`, `auth_method` (`shared_token`|`device_token`), `pairing`
  (`unknown`|`not_required`|`paired`|`pending`|`identity_required`) with the request id or the
  `identity_error`, and the deviceId (public: `openclaw devices list` shows it). This is the access
  **now**: a failed connect, a dropped connection and `close()` reset it to `unknown` (keeping the
  deviceId), and doctor then reads the identity file instead.

**Method → scope** (`core-method-policy`, 2026.10.5):

| Method | Scope |
|---|---|
| `chat.send`, `chat.abort` | `operator.write` |
| `chat.history`, `sessions.list`, `sessions.subscribe`, `sessions.preview`, `sessions.describe`, `agents.list`, `config.get`, `config.schema` | `operator.read` |
| `sessions.reset`, `sessions.compact`, `config.set`, `config.apply`, `config.patch`, `agents.create`, `agents.update`, `agents.delete` | `operator.admin` |
| `sessions.patch`, `sessions.delete` | dynamic (depends on the change) |

`operator.write` and `operator.admin` also satisfy `operator.read`.

**Session reset policy** (config, not a method): `session.reset: {mode: "daily" | "idle",
atHour: 0–23, idleMinutes}` (host local time; per type/channel in `resetByType`/`resetByChannel`);
the default is `none` (no automatic reset, compaction bounds the context). It is global to every
OpenClaw channel and resets the session under the **same key**.

**The shared token.** `openclaw config get gateway.auth.token` always prints it redacted (also with
`--json`) and does not resolve SecretRefs. The command that shows it is
**`openclaw gateway auth-token --show`** (resolves `gateway.auth.token`, `OPENCLAW_GATEWAY_TOKEN`
and SecretRefs; prints only in an interactive terminal on the OpenClaw host). `fogar-gw onboard`
says so.

## Change log of this file

- **2026-11-13**: added "Device identity, pairing and `operator.admin`" (server 2026.10.5, read in
  the package source; the local admin path probed live): the gateway asks for `operator.admin`,
  signs every `connect` (device auth v3), stores and prefers the device token issued after a
  remote pairing, falls back to the shared token when it is refused, and reports
  `PAIRING_REQUIRED` with the approval command. The token hint is `openclaw gateway auth-token --show`.
  Review the same day: onboard always tests the shared token, a missing device proof is its own
  error (`orchestrator_device_identity_required`), the access resets on failure/drop/close,
  unannounced scopes are unknown (`None`), broader scopes on the same token are stored, and the
  identity file is locked (`flock`) and copied by `migrate`. Signature verified live against a
  2026.10.5 on loopback (hello-ok read/write/admin, `shared_token`/`not_required`).

- **2026-11-06**: added "Connect deadline and `chat.send` session keys" (server 2026.11.1, read in
  the package source): the handshake deadline is now 30 s and `chat.send` rejects session keys
  that do not identify an agent; no gateway change needed.
- **2026-11-06**: added "Token rejected on `connect`, and `/healthz`" (server 2026.11.1, read in
  the package source); the spent startup budget now raises `OrchestratorStartingError`, and an
  `ok:false` answer to `connect` raises `OrchestratorRejectedError` (both keep the stable code).
- **2026-11-06**: added "OpenClaw still starting: `UNAVAILABLE` / `startup-sidecars`" (server
  2026.11.1): `connect()` retries that rejection on a new socket for up to 30 s; the `connect`
  frame carries `client.displayName: "fogar-gateway"`.
- **2026-11-01**: added "Agent failure after the turn was accepted" (issue #300): `chat`
  `state: "error"` and `agent` `phase: "error"` are terminal.
- **2026-10-28**: minimum supported server is now **2026.6.11** (issue #262). Removed the
  pre-2026.6.11 compatibility: a `res` with `ok:true` + `status: "done"` no longer ends a turn
  (only `chat` `state: "final"` or a `res` with `ok:false` does), and `hello-ok`'s
  `snapshot.agents[]` / `snapshot.defaultAgentId` are no longer read (roster only from
  `agents.list`, default agent from `agents.list`'s `defaultId` when present, else
  `snapshot.sessionDefaults.defaultAgentId`). Also dropped the unused frame builders `chat.history`,
  `chat.inject`, `sessions.steer`, `sessions.list`, `agent.identity.get`, `models.list` and
  `models.authStatus` (the methods themselves are unchanged server-side).
- **2026-10-23**: added "`runId`, `chat.abort` and a concurrent `chat.send`" (issue #219).
- **2026-10-20**: documented the official delta/replace/snapshot semantics (issue #243).
- **2026-10-20**: added "Breaking change — OpenClaw 2026.10.5" (issue #241): `final` carries the full text without `deltaText`.
- **2026-10-19**: added "Errors toward clients and handshake failures" (issue #228).
- **2026-10-18**: added "Frame size, malformed frames and connection end" (issue #213).

- **2026-10-08**: added "Session key carries the per-client identity" — documents the
  `agent:{agent}:{client.id}` convention introduced by #52/#105 (issue #119). No server-side
  protocol change; this records how the gateway uses `sessionKey`.
- **2026-07-18**: added "No per-session/per-message system-prompt hook exists" — researched
  while resolving issue #100. Confirmed against OpenClaw's own source (`chat.send`'s strict
  TypeBox schema, `chat.inject`'s assistant-role transcript-write behavior, `sessions.patch`'s
  full field list) that there is no wire-level way to set a per-client system prompt today.
- **2026-07-10**: added the issue #84 mitigation note — `FogarBridge` now collapses multiple
  OpenClaw agent start/end pairs into a single client-facing turn_start/turn_end. Previously
  the bridge was emitting one pair per agent event, causing the voice client (and any other client)
  to log 2–4 duplicate `turn_start`/`turn_end` frames per user message during tool use.
- **2026-07-03**: created. Documents the v2026.6.11 breaking changes (agents.list, chat
  completion signal), the `session.tool` event shape, and the multi-turn-per-message nuance —
  all discovered live while implementing PRs #70, #71, and the `tests/e2e/` suite (#74).
