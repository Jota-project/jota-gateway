import { describe, expect, it } from 'vitest'
import { backoffMs, ChatLog, closeAction, endReasonText, stopText } from './chat'

const start = (turn_id: string, extra: object = {}) => ({ type: 'turn_start', turn_id, turn_seq: 1, conversation: 'c1', origin: 'user', ...extra })

describe('ChatLog', () => {
  it('junta los tokens de un turno y lo cierra con su turn_end', () => {
    const log = new ChatLog('c1')
    log.addUser('¿Qué tiempo hace?')
    log.apply(start('t-1'))
    log.apply({ type: 'token', turn_id: 't-1', text: 'Sol' })
    expect(log.turnOpen).toBe(true)
    log.apply({ type: 'token', turn_id: 't-1', text: ' y 20 grados.' })
    log.apply({ type: 'turn_end', turn_id: 't-1', reason: 'done' })
    expect(log.turnOpen).toBe(false)
    expect(log.lines.map((l) => [l.role, l.text, l.state, l.reason])).toEqual([
      ['user', '¿Qué tiempo hace?', 'ended', 'done'],
      ['assistant', 'Sol y 20 grados.', 'ended', 'done'],
    ])
  })

  it('guarda por qué terminó un turno: cancelado, sustituido, timeout tras su error', () => {
    const log = new ChatLog('c1')
    log.apply(start('t-1'))
    log.apply({ type: 'turn_end', turn_id: 't-1', reason: 'cancelled' })
    log.apply(start('t-2'))
    log.apply({ type: 'error', code: 'TURN_ERROR', message: 'turn_timeout', fatal: false, turn_id: 't-2' })
    log.apply({ type: 'turn_end', turn_id: 't-2', reason: 'timeout' })
    expect(log.lines.map((l) => [l.reason, l.error])).toEqual([
      ['cancelled', undefined],
      ['timeout', 'turn_timeout'],
    ])
  })

  it('no acepta tokens de un turno ya cerrado ni de uno desconocido', () => {
    const log = new ChatLog('c1')
    log.apply(start('t-1'))
    log.apply({ type: 'turn_end', turn_id: 't-1', reason: 'replaced' })
    expect(log.apply({ type: 'token', turn_id: 't-1', text: 'tarde' })).toBe(false)
    expect(log.apply({ type: 'token', turn_id: 't-9', text: 'nadie' })).toBe(false)
    expect(log.lines[0]!.text).toBe('')
  })

  it('marca el push como Fogar hablando primero y avisa si es de otra conversación', () => {
    const log = new ChatLog('c1')
    log.apply(start('t-1', { origin: 'push' }))
    log.apply(start('t-2', { origin: 'push', conversation: 'ayer' }))
    expect(log.lines.map((l) => [l.origin, l.otherConversation])).toEqual([
      ['push', undefined],
      ['push', 'ayer'],
    ])
  })

  it('tras reconectar, un turn_id repetido es un turno nuevo', () => {
    const log = new ChatLog('c1')
    log.apply(start('t-1'))
    log.apply({ type: 'token', turn_id: 't-1', text: 'uno' })
    log.closeOpenTurns() // se cayó el socket sin turn_end
    log.apply(start('t-1'))
    log.apply({ type: 'token', turn_id: 't-1', text: 'dos' })
    expect(log.lines.map((l) => [l.text, l.state, l.reason])).toEqual([
      ['uno', 'ended', 'closed'],
      ['dos', 'streaming', undefined],
    ])
    expect(new Set(log.lines.map((l) => l.key)).size).toBe(2)
  })
})

describe('ChatLog al volver a leer lo dicho', () => {
  it('se queda solo con lo que sigue llegando, para no enseñar nada dos veces', () => {
    const log = new ChatLog('c1')
    log.addUser('uno')
    log.apply(start('t-1'))
    log.apply({ type: 'turn_end', turn_id: 't-1', reason: 'done' })
    log.addUser('dos')
    log.apply(start('t-2'))
    log.keepStreaming()
    expect(log.lines.map((l) => [l.role, l.state])).toEqual([['assistant', 'streaming']])
    log.apply({ type: 'token', turn_id: 't-2', text: 'sigue' })
    expect(log.lines[0]!.text).toBe('sigue')
  })
})

describe('endReasonText', () => {
  it('no dice nada si terminó bien', () => {
    expect(endReasonText({ reason: 'done' })).toBeNull()
  })
  it('cuenta cada motivo, con el error de turno si lo hubo', () => {
    expect(endReasonText({ reason: 'cancelled' })).toBe('La paraste aquí.')
    expect(endReasonText({ reason: 'interrupted' })).toMatch(/interrumpió/)
    expect(endReasonText({ reason: 'timeout' })).toMatch(/dejó de responder/)
    expect(endReasonText({ reason: 'error', error: 'orchestrator_unavailable' })).toBe('Falló: openclaw no está disponible.')
  })
  it('un motivo que la web no conoce es un error', () => {
    expect(endReasonText({ reason: 'algo_nuevo' })).toBe('Falló.')
  })
})

describe('closeAction', () => {
  it('«conversation opened elsewhere» nunca reconecta solo', () => {
    expect(closeAction(1008, 'conversation opened elsewhere')).toBe('elsewhere')
  })
  it('para con los 1008 que no se arreglan reconectando', () => {
    expect(closeAction(1008, 'conversation not found')).toBe('stop')
    expect(closeAction(1008, 'Clave de cliente invalida o inactiva.')).toBe('stop')
    expect(stopText('conversation not found')).toMatch(/ya no existe/)
  })
  it('reintenta tras caídas, reinicios y lentitud', () => {
    for (const code of [1006, 1011, 1012, 1013]) expect(closeAction(code, '')).toBe('retry')
  })
  it('con el acceso retirado para y lo dice: lo decide la persona', () => {
    expect(closeAction(1008, 'revoked')).toBe('stop')
    expect(stopText('revoked')).toMatch(/Se retiró el acceso del panel/)
  })
  it('un cierre normal (inactividad) espera a que escribas', () => {
    expect(closeAction(1000, '')).toBe('idle')
  })
  it('espera cada vez más, hasta 30 s', () => {
    expect([1, 2, 3, 10].map(backoffMs)).toEqual([1000, 2000, 4000, 30_000])
  })
})
