import type { ClientType, PairingState, ServiceState } from '@/api/types'

// Todos los textos de los valores del gateway, en un solo sitio (spec «Web en Vue»).
// Se completan bloque a bloque: motivos de cierre (17b), etapas (17c), errores de turno (17e)…

export const SERVICE_NAME = {
  orchestrator: 'OpenClaw',
  stt: 'Transcripción',
  tts: 'Voz de Fogar',
} as const

export const SERVICE_STATE: Record<ServiceState, string> = {
  CONNECTED: 'Conectado',
  RECONNECTING: 'Reconectando',
  DEGRADED: 'Caído',
}

export const PROVIDER: Record<string, string> = {
  fogar: 'Fogar',
  elevenlabs: 'ElevenLabs',
  off: 'Desactivado',
}

export const CLIENT_TYPE: Record<ClientType, string> = {
  ha: 'Home Assistant',
  esp32: 'ESP32',
  web: 'Web',
  app: 'App',
  screen: 'Pantalla',
}

export const PAIRING_STATE: Record<PairingState, string> = {
  open: 'Esperando',
  used: 'Emparejado',
  expired: 'Caducado',
  cancelled: 'Anulado',
}

/** Texto de un valor, o el valor tal cual si el gateway trae uno nuevo que la web aún no conoce. */
export function label(texts: Record<string, string>, value: string): string {
  return ownText(texts, value) ?? value
}

/**
 * El texto de `value` solo si es una clave propia de `texts`: un valor que llega del gateway como
 * `constructor` o `toString` no debe dar con los miembros de `Object.prototype`.
 */
export function ownText(texts: Record<string, string>, value: string): string | undefined {
  return Object.hasOwn(texts, value) ? texts[value] : undefined
}

/** Motivos de cierre de sesión (src/services/stats.py, CloseReason). */
export const CLOSE_REASON: Record<string, string> = {
  client_disconnect: 'Se fue el cliente',
  idle_timeout: 'Inactividad (5 min)',
  silence_timeout: 'Silencio al hablar',
  slow_client: 'Cliente lento',
  revoked: 'Acceso retirado',
  shutdown: 'Apagado del gateway',
  error: 'Error',
  unknown: 'Desconocido',
}

/** Conexiones rechazadas antes de empezar (src/api/routes.py). */
export const HANDSHAKE_REJECTED: Record<string, string> = {
  invalid_key: 'Clave no válida o inactiva',
  timeout: 'Sin mensaje inicial a tiempo',
  invalid: 'Mensaje inicial no válido',
  too_large: 'Mensaje inicial demasiado grande',
  not_text: 'Mensaje inicial binario',
  agent: 'Agente no permitido',
}

/** Errores de turno (docs/client-protocol.md §9). */
export const TURN_ERROR: Record<string, string> = {
  orchestrator_unavailable: 'OpenClaw no está disponible',
  orchestrator_send_failed: 'No se pudo enviar a OpenClaw',
  orchestrator_error: 'OpenClaw rechazó la pregunta',
  orchestrator_disconnected: 'OpenClaw se desconectó a mitad',
  turn_timeout: 'OpenClaw dejó de responder',
  turn_overflow: 'El gateway no pudo seguir el ritmo de la respuesta',
  turn_in_progress: 'Ya había un turno en marcha',
  internal_error: 'Error interno del gateway',
}

/** En qué está un turno (`turn_progress`, docs/client-protocol.md §5); la herramienta va aparte. */
export const TURN_PROGRESS: Record<string, string> = {
  preparing: 'Preparando…',
  thinking: 'Pensando…',
}

/** Modos de entrada y salida de una sesión. */
export const MODE: Record<string, string> = {
  audio: 'Voz',
  text: 'Texto',
  status: 'Estado',
}

/** Qué está haciendo una sesión ahora (`turn.kind`). */
export const TURN_KIND: Record<string, string> = {
  idle: 'En espera',
  normal: 'Respondiendo',
  push: 'Fogar habla',
}

/** Etapas de un turno (src/services/pipeline_tracker.py). */
export const STAGE: Record<string, string> = {
  transcription_partial: 'Transcripción parcial',
  transcription_final: 'Transcripción final',
  transcription_incomplete: 'Transcripción incompleta',
  llm_start: 'Pregunta a OpenClaw',
  llm_first_token: 'Primer token',
  llm_done: 'Respuesta completa',
  tts_start: 'Empieza la voz',
  tts_first_chunk: 'Primer audio',
  tts_done: 'Voz terminada',
  barge_in: 'Interrupción',
  client_slow: 'Cliente lento',
  session_revoked: 'Acceso retirado',
  session_end: 'Fin de la sesión',
}

/** Estado de una sesión guardada (`/admin/sessions`). */
export const SESSION_STATUS: Record<string, string> = {
  active: 'Abierta',
  completed: 'Cerrada',
  error: 'Con error',
  revoked: 'Acceso retirado',
}
