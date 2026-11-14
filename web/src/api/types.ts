// Espejo de lo que devuelve el gateway. Fuente: src/api/admin_routes.py (_build_state),
// src/services/stats.py y src/services/bridge.py (snapshot). Si el gateway cambia, cambia aquí.

/** Estado interno de un servicio (src/services/reconnection.py, ConnectionState). */
export type ServiceState = 'CONNECTED' | 'RECONNECTING' | 'DEGRADED'

export interface ServiceSnapshot {
  state: ServiceState
  last_error: string | null
}

export interface SpeechServiceSnapshot extends ServiceSnapshot {
  provider: string // 'fogar' | 'elevenlabs' | 'off'
}

export type OutputMode = 'audio' | 'text' | 'status'

export interface LiveSession {
  session_id: string
  client_id: string
  client_name: string
  /** La conversación de la sesión (M19). */
  conversation?: { id: string; agent: string }
  input_mode: 'audio' | 'text'
  output_mode: OutputMode[]
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

/** Con qué acceso está el gateway en OpenClaw (M19, `OrchestratorAccess.as_dict()`); sin secretos. */
export type OpenClawPairing = 'unknown' | 'not_required' | 'paired' | 'pending' | 'identity_required'

export interface OpenClawAccess {
  /** `null`: sin conexión, o OpenClaw no los anuncia (desconocidos, no «ninguno»). */
  scopes: string[] | null
  admin: boolean
  auth_method: 'shared_token' | 'device_token' | null
  pairing: OpenClawPairing
  pairing_request_id: string | null
  identity_error: string | null
  /** Público: identifica al gateway como dispositivo en OpenClaw. */
  device_id: string | null
}

export interface GatewayState {
  gateway: { started_at: string; uptime_s: number; version?: string } // version desde v1.29.0
  services: {
    orchestrator: ServiceSnapshot & Partial<OpenClawAccess>
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

// Clientes: src/models/admin_schemas.py. `client_type` se valida solo al escribir (ClientType);
// al leer es texto libre por filas antiguas.
export type ClientType = 'ha' | 'esp32' | 'web' | 'app' | 'screen'

/** Lectura de un cliente: nunca lleva la clave, solo su huella `key_fp` (8 hex). */
export interface ClientResponse {
  id: string
  name: string
  key_fp: string
  is_active: boolean
  client_type: string | null
  default_agent: string | null
  allowed_agents: string[] | null // null = sin límite, [] = ninguno
  created_at: string
  stt_language: string
  stt_vad_thold: number
  tts_voice: string
  tts_speed: number
  barge_in_enabled: boolean
  barge_in_min_chars: number
  output_mode: OutputMode[] | null
  silence_timeout_s: number
  max_silence_turns: number
  push_enabled: boolean
  tool_calls_enabled: boolean
}

/** Crear y rotar: la única vez que llega la clave. Nunca se guarda en un store de admin ni se loguea. */
export interface ClientWithKey extends ClientResponse {
  client_key: string
}

// Emparejamiento: src/api/pairing_routes.py. Códigos de 6 cifras, 10 min, hasta 5 abiertos.
export type PairingState = 'open' | 'used' | 'expired' | 'cancelled'

/** Plantilla de `POST /admin/pairing`: los campos de crear un cliente, todos opcionales y sin clave. */
export type PairingTemplate = Partial<Omit<ClientResponse, 'id' | 'key_fp' | 'is_active' | 'created_at' | 'client_type'>> & {
  client_type?: ClientType | null
}

/** `GET /admin/pairing` y `/{id}`: nunca lleva el código. */
export interface PairingSummary {
  id: string
  state: PairingState
  expires_at: string
  client_id: string | null
  client_name: string | null
  template: PairingTemplate
}

/** `POST /admin/pairing`: el código solo llega aquí. */
export interface PairingOpened extends PairingSummary {
  code: string
}

/** `/admin/orchestrator/status`, `/admin/transcriber/status`, `/admin/tts/status`. */
export interface ServiceDetail {
  name: string
  provider?: string
  state: ServiceState
  connected_at: string | null
  reconnect_attempts: number
  last_error: string | null
}

/** `/ready`: sondea de verdad cada servicio. 503 (con este cuerpo) si cae OpenClaw. */
export interface ReadyResponse {
  status: 'ok' | 'degraded' | 'unavailable'
  services: Record<'orchestrator' | 'transcriber' | 'tts', 'ok' | 'unavailable'>
}

/** `POST /admin/clients` (sin clave: la genera el gateway) y `PATCH` (solo lo que cambia). */
export type ClientInput = Partial<Omit<ClientResponse, 'id' | 'key_fp' | 'created_at'>>

// Sesiones en memoria: src/api/admin_routes.py (_session_summary, _session_detail).
export type SessionStatus = 'active' | 'completed' | 'error' | 'revoked'

export interface SessionSummary {
  session_id: string
  client_id: string
  status: SessionStatus
  input_mode: 'audio' | 'text'
  output_mode: OutputMode[]
  started_at: string
  ended_at: string | null
  turn_count: number
  last_latencies: { llm_first_token_ms: number | null; tts_first_chunk_ms: number | null; turn_e2e_ms: number | null }
}

export interface PipelineEventRecord {
  stage: string
  ts_ms: number
  /** Desde el evento anterior de la sesión. */
  elapsed_ms: number
  turn: number
  meta: Record<string, unknown>
}

export interface SessionDetail extends SessionSummary {
  summary: { turn_count: number; duration_s: number | null; avg_llm_first_token_ms: number | null; avg_turn_e2e_ms: number | null }
  events: PipelineEventRecord[]
}

export interface SessionList {
  active: number
  total: number
  sessions: SessionSummary[]
}

// Conversaciones (M19): src/api/conversation_routes.py. El gateway no guarda texto: título y mensajes
// los lee de OpenClaw en cada petición.
export type ConversationKind = 'default' | 'created' | 'legacy'

export interface Conversation {
  id: string
  client_id: string
  client_name: string
  agent: string
  /** `default`: la del día; `created`: elegida o nueva; `legacy`: de antes de M19. */
  kind: ConversationKind | string
  day: string | null
  created_at: string
  last_turn_at: string | null
  turn_count: number
  /** De OpenClaw; `null` si no lo tiene o no responde. */
  title: string | null
}

export interface ConversationMessage {
  role: 'user' | 'assistant'
  text: string
  timestamp: string | null
}

export interface ConversationMessages {
  conversation_id: string
  messages: ConversationMessage[]
  has_more: boolean
}

/** `GET /admin/conversations/agents`: con qué agentes puede empezar el panel y cuál tocaría sin elegir. */
export interface ConversationAgents {
  agents: string[]
  default: string | null
}
