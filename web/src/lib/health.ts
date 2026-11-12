import type { StreamConnection } from '@/api/stateStream'
import type { GatewayState, ServiceSnapshot } from '@/api/types'

// Lectura del estado de los servicios para la pantalla Estado, la barra lateral y las luces del fondo.

/** `off`: el proveedor de voz está desactivado a propósito (el gateway lo da como DEGRADED). */
export type ServiceTone = 'ok' | 'warn' | 'bad' | 'off'

export function serviceTone(service: ServiceSnapshot & { provider?: string }): ServiceTone {
  if (service.provider === 'off') return 'off'
  if (service.state === 'CONNECTED') return 'ok'
  return service.state === 'RECONNECTING' ? 'warn' : 'bad'
}

function services(state: GatewayState) {
  const s = state.services
  return [s.orchestrator, s.stt, s.tts]
}

/** Todo responde (lo desactivado a propósito no cuenta como fallo). */
export function isHealthy(state: GatewayState): boolean {
  return services(state).every((service) => ['ok', 'off'].includes(serviceTone(service)))
}

const SUBJECT = { orchestrator: 'OpenClaw', stt: 'La transcripción', tts: 'La voz de Fogar' } as const

function sentence(subject: string, tone: ServiceTone): string {
  return `${subject} ${tone === 'warn' ? 'se está reconectando' : 'no responde'}`
}

/** El titular de Estado: la verdad del estado en una frase. */
export function headline(state: GatewayState | null, connection: StreamConnection): string {
  if (connection === 'offline') return 'No llego al gateway'
  if (!state) return 'Conectando…'
  const orchestrator = serviceTone(state.services.orchestrator)
  if (orchestrator !== 'ok') return sentence(SUBJECT.orchestrator, orchestrator)
  const failing = (['stt', 'tts'] as const)
    .map((key) => [key, serviceTone(state.services[key])] as const)
    .filter(([, tone]) => tone === 'warn' || tone === 'bad')
  if (failing.length === 2) return 'La voz tiene problemas'
  const [only] = failing
  return only ? sentence(SUBJECT[only[0]], only[1]) : 'Todo funciona'
}
