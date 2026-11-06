// Espejo de lo que devuelve el gateway. Fuente: src/api/admin_routes.py (_build_state),
// src/services/stats.py y src/services/bridge.py (snapshot). Si el gateway cambia, cambia aquí.

/** Estado interno de un servicio (src/services/reconnection.py, ConnectionState). */
export type ServiceState = 'CONNECTED' | 'RECONNECTING' | 'DEGRADED'

export interface ServiceSnapshot {
  state: ServiceState
  last_error: string | null
}

export interface SpeechServiceSnapshot extends ServiceSnapshot {
  provider: string // 'jota' | 'elevenlabs'
}

export interface LiveSession {
  session_id: string
  client_id: string
  client_name: string
  input_mode: 'audio' | 'text'
  output_mode: Array<'audio' | 'text' | 'status'>
  started_at: string | null
  age_s: number
  turn: { kind: 'idle' | 'normal' | 'push'; since_s: number | null }
  turn_count: number
  sender: { pending_msgs: number; pending_bytes: number; peak_msgs: number; peak_bytes: number }
  turn_queue: { depth: number; peak: number }
  push_backlog: { pending: number; peak: number }
  last_activity_s: number | null
  stt_state: ServiceState | null
  last_latencies: {
    llm_first_token_ms: number | null
    tts_first_chunk_ms: number | null
    turn_e2e_ms: number | null
  } | null
}

export interface GatewayState {
  gateway: { started_at: string; uptime_s: number }
  services: {
    orchestrator: ServiceSnapshot
    stt: SpeechServiceSnapshot
    tts: SpeechServiceSnapshot
  }
  totals: {
    sessions_opened: number
    sessions_closed: Record<string, number>
    handshake_rejected: Record<string, number>
    turns: { normal: number; push: number }
    turn_errors: Record<string, number>
    sessions_active: number
  }
  sessions: LiveSession[]
}
