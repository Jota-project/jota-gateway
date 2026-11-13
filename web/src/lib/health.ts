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

/**
 * La línea de debajo del titular: el hallazgo y qué pasa mientras tanto. Habla del peor servicio; si
 * todo va bien, de lo que pasa ahora. `intentos` sale del detalle del servicio (puede no haber llegado).
 */
export function finding(
  state: GatewayState | null,
  connection: StreamConnection,
  intentos: Partial<Record<'orchestrator' | 'stt' | 'tts', number>> = {},
): string {
  if (connection === 'offline') return 'Lo vuelvo a intentar solo. Si no vuelve, comprueba que el gateway está encendido.'
  if (!state) return ''
  const n = (key: 'orchestrator' | 'stt' | 'tts') => (intentos[key] ? ` Lleva ${intentos[key]} intentos.` : '')
  if (serviceTone(state.services.orchestrator) !== 'ok')
    return `Fogar no puede contestar a nadie hasta que vuelva. Se reintenta solo.${n('orchestrator')}`
  const tts = serviceTone(state.services.tts)
  const stt = serviceTone(state.services.stt)
  if (tts === 'warn' || tts === 'bad') return `Mientras tanto, Fogar contesta por texto.${n('tts')}`
  if (stt === 'warn' || stt === 'bad') return `Mientras tanto, nadie puede hablarle por voz; por texto sí.${n('stt')}`
  const conectados = state.sessions.length
  const abiertos = state.sessions.filter((s) => s.turn.kind !== 'idle').length
  const apagada = tts === 'off' || stt === 'off' ? ' La voz está apagada en la configuración.' : ''
  if (!conectados) return `Ningún aparato conectado ahora.${apagada}`
  const aparatos = `${conectados} ${conectados === 1 ? 'aparato conectado' : 'aparatos conectados'}`
  const turnos = abiertos ? `, ${abiertos} ${abiertos === 1 ? 'turno abierto' : 'turnos abiertos'}` : ''
  return `${aparatos}${turnos}.${apagada}`
}
