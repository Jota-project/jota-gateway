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

> Since 2026.10.5 `final` also carries the full reply text and no `deltaText` — see
> "Breaking change — OpenClaw 2026.10.5" below.

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
- Snapshots after the first arrive **at most once every 2 s** per session and connection with
  the latest text; the terminal event flushes the last snapshot immediately, **including final
  corrections or retractions**.
- `final`, `aborted` and `error` keep full-message semantics.

**Deliberate gateway decision:** the gateway emits a text tail only for `final`. On
`aborted` and `error` it does **not** emit any tail from `message`: after an `aborted` the
client has already cut the reply (barge-in or cancel), and speaking more text after the user
interrupted would be wrong; an `error` may omit `message` on purpose. Pinned by a test.

How the gateway responds (`src/services/openclaw/chat_text.py`, used by
`OpenClawClient.stream_response()` and `JotaBridge.deliver_push()`): `ChatTextTracker` keeps
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
16-character delta, a 409-character final), so TTS starts only when generation ends; fixing
that latency is an OpenClaw streaming-configuration matter.

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

## Session key carries the per-client identity (jota-gateway convention)

Every turn jota-gateway sends over `chat.send` uses `sessionKey = "agent:{agent}:{client.id}"`,
where `client.id` is the `ClientRecord` UUID of the authenticated caller (WS handshake or REST
`/v1/*` with a Bearer key), not a shared global key. This is a jota-gateway convention layered on
OpenClaw's opaque `sessionKey` string, not something the OpenClaw server enforces or validates.
Consequences observable on the wire:

- Two clients talking to the same agent get independent OpenClaw sessions (separate history).
- Events come back tagged with that same `sessionKey`; the gateway recovers the client with
  `sk.rsplit(":", 1)[-1]` (safe for agent names or keys containing extra colons).
- The REST trusted-origin legacy path (no Bearer) has no client row, so it uses the fixed id `ha`.

How the `agent` segment is resolved and validated lives in `CLAUDE.md` ("Session key derivation").

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
  `ok:false` is a failure that ends the turn; the explicit `status: "done"` is kept as a legacy
  completion fallback (pre-2026.6.11). Any other `ok:true` status is not a turn end; the real
  completion is the `chat` event with `state == "final"`.
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
retried by the reconnect loop like any failed connect. `ReconnectingOpenClawClient`'s
`last_error` (exposed by `GET /admin/orchestrators/{name}/status`) holds that code, or just the
exception type name (`OSError`, `TimeoutError`...) for anything else. The same `last_error`
rule applies to every wrapper that goes through `reconnect_with_backoff`.

For operator diagnostics, a rejected `hello`, a failed `agents.list` and a rejected turn log one
WARNING line with OpenClaw's `error.code` (`code=...`), only when it matches
`[A-Za-z0-9_.:-]{1,48}`; otherwise `code=unspecified`. `error.message` and the rest of the body
are never read.

Note: the `websockets` library itself logs raw frames at DEBUG on its own `websockets.*`
loggers; keep those at their default level in production.

## `runId`, `chat.abort` and a concurrent `chat.send` (2026-10-22/23, server 2026.10.5, issue #219)

Observed live on green-house with throwaway session keys; structure only (no text recorded).
Full notes in the vault (`JGW Observaciones 2026-10 - OpenClaw y cliente lento en Linux`).

- **Every `chat` and `agent` event carries `runId`** (and `seq`), including `chat` `aborted`.
  The `chat.send` ack arrives in 30–60 ms as `res ok` with `payload {runId, status}`, so the
  run id is known before any event of the run.
- **Non-terminal `chat` states exist:** `state:"status"` with `phase`
  (`preparing_workspace`, `preparing_context`, `starting_model`). Terminal states seen:
  `final` (with `stopReason`) and `aborted`; `error` was not observed here. Do **not** treat
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

**Breaking for jota-gateway as of v1.21.0:** `stream_response()` ends a turn on the first
`chat` `final` of its `sessionKey` and its queue receives every `chat` event of that key,
regardless of `runId`. With a push in flight, a user `send` therefore receives the push's
deltas and is closed by whichever `final` comes first, and the rest of the other run is
lost. Fix belongs to M9 (correlate by `runId`; issue #202).

---

## Change log of this file

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
- **2026-07-10**: added the issue #84 mitigation note — `JotaBridge` now collapses multiple
  OpenClaw agent start/end pairs into a single client-facing turn_start/turn_end. Previously
  the bridge was emitting one pair per agent event, causing jota-voice (and any other client)
  to log 2–4 duplicate `turn_start`/`turn_end` frames per user message during tool use.
- **2026-07-03**: created. Documents the v2026.6.11 breaking changes (agents.list, chat
  completion signal), the `session.tool` event shape, and the multi-turn-per-message nuance —
  all discovered live while implementing PRs #70, #71, and the `tests/e2e/` suite (#74).
