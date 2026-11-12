import { describe, expect, it } from 'vitest'
import type { GatewayState, ServiceState } from '@/api/types'
import { headline, isHealthy, serviceTone } from './health'

function state(orch: ServiceState, stt: ServiceState, tts: ServiceState, sttProvider = 'fogar', ttsProvider = 'fogar'): GatewayState {
  return {
    gateway: { started_at: '2026-11-09T10:00:00+00:00', uptime_s: 60, version: '1.29.0' },
    services: {
      orchestrator: { state: orch, last_error: null },
      stt: { state: stt, last_error: null, provider: sttProvider },
      tts: { state: tts, last_error: null, provider: ttsProvider },
    },
    totals: {
      sessions_opened: 0,
      sessions_closed: {},
      handshake_rejected: {},
      turns: { normal: 0, push: 0 },
      turn_errors: {},
      sessions_active: 0,
    },
    sessions: [],
  }
}

describe('serviceTone', () => {
  it('un proveedor desactivado no es un fallo, aunque el gateway lo dé como DEGRADED', () => {
    expect(serviceTone({ state: 'DEGRADED', last_error: 'disabled', provider: 'off' })).toBe('off')
  })
  it.each([
    ['CONNECTED', 'ok'],
    ['RECONNECTING', 'warn'],
    ['DEGRADED', 'bad'],
  ] as const)('%s → %s', (s, tone) => {
    expect(serviceTone({ state: s, last_error: null })).toBe(tone)
  })
})

describe('isHealthy', () => {
  it('con la voz desactivada a propósito, todo sigue sano', () => {
    expect(isHealthy(state('CONNECTED', 'DEGRADED', 'DEGRADED', 'off', 'off'))).toBe(true)
  })
  it('un servicio que reconecta no está sano', () => {
    expect(isHealthy(state('CONNECTED', 'RECONNECTING', 'CONNECTED'))).toBe(false)
  })
})

describe('headline', () => {
  it.each([
    [null, 'offline', 'No llego al gateway'],
    [null, 'connecting', 'Conectando…'],
    [state('CONNECTED', 'CONNECTED', 'CONNECTED'), 'live', 'Todo funciona'],
    [state('CONNECTED', 'DEGRADED', 'DEGRADED', 'off', 'off'), 'live', 'Todo funciona'],
    [state('RECONNECTING', 'CONNECTED', 'CONNECTED'), 'live', 'OpenClaw se está reconectando'],
    [state('DEGRADED', 'CONNECTED', 'CONNECTED'), 'live', 'OpenClaw no responde'],
    [state('CONNECTED', 'DEGRADED', 'CONNECTED'), 'live', 'La transcripción no responde'],
    [state('CONNECTED', 'CONNECTED', 'RECONNECTING'), 'live', 'La voz de Fogar se está reconectando'],
    [state('CONNECTED', 'DEGRADED', 'DEGRADED'), 'live', 'La voz tiene problemas'],
  ] as const)('%#: %s', (s, connection, expected) => {
    expect(headline(s, connection)).toBe(expected)
  })
  it('sin conexión manda aunque quede la última instantánea', () => {
    expect(headline(state('CONNECTED', 'CONNECTED', 'CONNECTED'), 'offline')).toBe('No llego al gateway')
  })
})
