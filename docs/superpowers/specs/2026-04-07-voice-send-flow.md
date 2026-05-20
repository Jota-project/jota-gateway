# Spec: Voice → Review → Send Flow (jota-float client)

## Context

The gateway now decouples transcription from orchestrator dispatch.
The client is responsible for showing the transcription, letting the user
review/edit it, and explicitly sending it with a `{"type": "send"}` WS message.

## State machine

```
IDLE
  │  user presses mic button
  ▼
RECORDING
  │  user releases / VAD silence / presses stop
  │  → gateway sends {"type": "end"}
  ▼
TRANSCRIBING          ← show spinner / "procesando..."
  │  gateway sends {"type": "transcription", "text": "..."}
  ▼
AWAITING_SEND         ← show editable transcription + Send button
  │  user optionally edits text
  │  user presses Send
  │  → client sends {"type": "send", "text": "<final text>"}
  ▼
STREAMING             ← show streaming tokens, disable all input
  │  gateway streams {"type": "token", "content": "..."}
  │  gateway sends {"type": "status", ...} when done
  ▼
IDLE                  ← ready for next turn
```

## WebSocket messages involved

### Client → Gateway
| Message | When |
|---|---|
| `{"type": "end"}` | User stops recording (mic button released / stop pressed) |
| `{"type": "send", "text": "..."}` | User confirms/edits transcription and sends |

### Gateway → Client
| Message | Meaning |
|---|---|
| `{"type": "transcription_partial", "text": "..."}` | Live partial (show greyed out) |
| `{"type": "transcription", "text": "..."}` | Final transcription — enter AWAITING_SEND |
| `{"type": "token", "content": "..."}` | Orchestrator streaming token |
| `{"type": "status", ...}` | Turn complete / error |
| `{"type": "service_status", "service": "...", "status": "..."}` | Service warning (non-fatal unless service=orchestrator) |
| `{"type": "interrupted"}` | Barge-in confirmed |

## UI behaviour per state

### IDLE
- Mic button: enabled, primary style
- Send button: hidden
- Transcription field: hidden or shows previous turn (read-only)
- Token output: shows previous response

### RECORDING
- Mic button: active/recording style (pulsing), tap to stop
- Send button: hidden
- All other actions: disabled
- Partials: show in transcription field (greyed, live)

### TRANSCRIBING
- All input disabled
- Show spinner or "procesando..." overlay on transcription area
- Partials may still arrive — keep showing them

### AWAITING_SEND
- Transcription field: **editable**, pre-filled with final transcription text
- Send button: visible and enabled, primary style
- Mic button: disabled (only one turn at a time)
- User can freely edit the text before sending
- No timeout — wait indefinitely for user action

### STREAMING
- All input disabled
- Send button: hidden
- Mic button: disabled
- Tokens stream into the response area

## Transitions

```
IDLE         → RECORDING     on mic button press (open WS if not open, send handshake)
RECORDING    → TRANSCRIBING  on mic button release / stop / VAD → send {"type":"end"}
TRANSCRIBING → AWAITING_SEND on {"type": "transcription"} received
AWAITING_SEND → STREAMING    on Send button press → send {"type":"send","text":"..."}
STREAMING    → IDLE          on {"type":"status","status":"done"} or WS close
any state    → IDLE          on WS error / disconnect (show error toast)
```

## Error handling

- `service_status` with `service: "transcriber"` or `service: "tts"` → show non-blocking toast, stay in current state
- `service_status` with `service: "orchestrator"` → show error, go to IDLE
- `{"type": "status", "status": "error"}` from orchestrator → show error in response area, go to IDLE
- WS unexpectedly closes during RECORDING/TRANSCRIBING → go to IDLE, show "Connection lost" toast

## Implementation notes for the agent

1. **State variable**: use a single `sessionState` enum/string, not scattered booleans
2. **Transcription field**: must be a proper `<textarea>` or equivalent — not a `<div>` — so the user can place cursor and edit
3. **Send button**: sends `{"type": "send", "text": transcriptionField.value.trim()}` — use the current field value, not the original transcription
4. **Partial display**: partials should not interfere with editing once in AWAITING_SEND — ignore partials in that state
5. **WS lifecycle**: keep the WS open across the full turn (RECORDING → IDLE). Do NOT reconnect between transcription and send.
6. **Previous `send to chat` button**: can be repurposed as the new Send button in AWAITING_SEND state, or removed. It must NOT auto-send — it must be driven by user action after seeing the transcription.
7. **Accessibility**: Send button label should be "Enviar" (not "Send to chat" or similar)

## Files likely to change in jota-float

- The main voice/chat component handling WS messages
- The mic button component (state-driven)
- Any existing "send to chat" handler (repurpose or remove)
- CSS/styles for AWAITING_SEND state (editable field highlight)
