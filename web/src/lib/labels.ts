import type { ServiceState } from '@/api/types'

// Todos los textos de los valores del gateway, en un solo sitio (spec «Web en Vue»).
// Se completan bloque a bloque: motivos de cierre (17b), etapas (17c), errores de turno (17e)…

export const SERVICE_NAME = {
  orchestrator: 'OpenClaw',
  stt: 'Transcripción',
  tts: 'Voz de Jota',
} as const

export const SERVICE_STATE: Record<ServiceState, string> = {
  CONNECTED: 'Conectado',
  RECONNECTING: 'Reconectando',
  DEGRADED: 'Caído',
}

export const PROVIDER: Record<string, string> = {
  jota: 'jota',
  elevenlabs: 'ElevenLabs',
}
