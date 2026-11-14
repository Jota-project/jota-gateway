import { describe, expect, it } from 'vitest'
import { backoffMs, ChatLog, closeAction, endReasonText, progressText, stopText } from './chat'

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

describe('ChatLog con turn_progress', () => {
  const progress = (turn_id: string, phase: string, tool: string | null = null) => ({ type: 'turn_progress', turn_id, phase, tool })

  it('guarda en qué está la respuesta mientras no llega texto; cada uno sustituye al anterior', () => {
    const log = new ChatLog('c1')
    log.apply(start('t-1'))
    expect(log.apply(progress('t-1', 'preparing'))).toBe(true)
    expect(log.lines[0]!.progress).toEqual({ phase: 'preparing', tool: null })
    log.apply(progress('t-1', 'tool', 'ha_call_service'))
    expect(log.lines[0]!.progress).toEqual({ phase: 'tool', tool: 'ha_call_service' })
  })

  it('el primer token lo quita, y vuelve si usa otra herramienta a media respuesta', () => {
    const log = new ChatLog('c1')
    log.apply(start('t-1'))
    log.apply(progress('t-1', 'thinking'))
    log.apply({ type: 'token', turn_id: 't-1', text: 'Voy' })
    expect(log.lines[0]!.progress).toBeNull()
    log.apply(progress('t-1', 'tool', 'read'))
    expect(log.lines[0]!.progress).toEqual({ phase: 'tool', tool: 'read' })
  })

  it('el turn_end lo quita, y el cierre del socket también', () => {
    const log = new ChatLog('c1')
    log.apply(start('t-1'))
    log.apply(progress('t-1', 'thinking'))
    log.apply({ type: 'turn_end', turn_id: 't-1', reason: 'done' })
    expect(log.lines[0]!.progress).toBeNull()
    log.apply(start('t-2'))
    log.apply(progress('t-2', 'thinking'))
    log.closeOpenTurns()
    expect(log.lines[1]!.progress).toBeNull()
  })

  it('no acepta progreso de un turno cerrado ni de uno desconocido', () => {
    const log = new ChatLog('c1')
    log.apply(start('t-1'))
    log.apply({ type: 'turn_end', turn_id: 't-1', reason: 'cancelled' })
    expect(log.apply(progress('t-1', 'thinking'))).toBe(false)
    expect(log.apply(progress('t-9', 'thinking'))).toBe(false)
    expect(log.lines[0]!.progress).toBeNull()
  })

  it('pipeline_event no cambia nada de la conversación', () => {
    const log = new ChatLog('c1')
    log.apply(start('t-1'))
    expect(log.apply({ type: 'pipeline_event', stage: 'llm_start', elapsed_ms: 3, turn: 1 })).toBe(false)
  })
})

describe('progressText', () => {
  it('dice cada fase en una frase corta', () => {
    expect(progressText({ phase: 'preparing', tool: null })).toBe('Preparando…')
    expect(progressText({ phase: 'thinking', tool: null })).toBe('Pensando…')
    expect(progressText({ phase: 'tool', tool: 'ha_call_service' })).toBe('Usando ha_call_service…')
  })
  it('una herramienta sin nombre es «una herramienta»', () => {
    expect(progressText({ phase: 'tool', tool: null })).toBe('Usando una herramienta…')
  })
  it('una fase que la web no conoce es un «trabajando» genérico', () => {
    expect(progressText({ phase: 'compacting', tool: null })).toBe('Trabajando…')
  })
  it('ni con nombres de Object.prototype (constructor, toString, __proto__)', () => {
    for (const phase of ['constructor', 'toString', '__proto__', 'hasOwnProperty']) {
      expect(progressText({ phase, tool: null })).toBe('Trabajando…')
    }
    expect(endReasonText({ reason: 'constructor' })).toBe('Falló.')
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
  it('«conversation opened elsewhere» (4102) nunca reconecta solo', () => {
    expect(closeAction(4102)).toBe('elsewhere')
  })
  it('para con 4000–4899 (también los que no conoce) y 1008, sin mirar el texto', () => {
    for (const code of [4000, 4001, 4002, 4010, 4020, 4021, 4100, 4101, 4200, 4350, 4899, 1008]) {
      expect(closeAction(code)).toBe('stop')
    }
    expect(stopText(4100)).toMatch(/ya no existe/)
    expect(stopText(4010)).toMatch(/está desactivado/)
    expect(stopText(4101)).toMatch(/no coincide/)
    expect(stopText(4321)).toBe('El gateway no aceptó la conexión del chat.')
  })
  it('reintenta tras caídas, fallos, reinicios, lentitud y un handshake a destiempo', () => {
    for (const code of [1006, 1011, 1012, 1013, 4900, 4950]) expect(closeAction(code)).toBe('retry')
  })
  it('con el acceso retirado (4200) para y lo dice: lo decide la persona', () => {
    expect(closeAction(4200)).toBe('stop')
    expect(stopText(4200)).toMatch(/Se retiró el acceso del panel/)
  })
  it('un cierre normal (inactividad) espera a que escribas', () => {
    expect(closeAction(1000)).toBe('idle')
  })
  it('espera cada vez más, hasta 30 s', () => {
    expect([1, 2, 3, 10].map(backoffMs)).toEqual([1000, 2000, 4000, 30_000])
  })
})
