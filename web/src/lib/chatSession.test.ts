import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createChatSession } from './chatSession'

// Un WebSocket de mentira: guarda lo enviado y deja simular lo que manda el gateway.
class FakeSocket {
  static all: FakeSocket[] = []
  sent: unknown[] = []
  closedWith: number | null = null
  onopen: (() => void) | null = null
  onmessage: ((e: { data: unknown }) => void) | null = null
  onclose: ((e: { code: number; reason: string }) => void) | null = null
  constructor(readonly url: string) {
    FakeSocket.all.push(this)
  }
  send(data: string) {
    this.sent.push(JSON.parse(data))
  }
  close(code: number) {
    this.closedWith = code
  }
  // Lado del gateway
  open() {
    this.onopen?.()
  }
  receive(msg: object) {
    this.onmessage?.({ data: JSON.stringify(msg) })
  }
  serverClose(code: number, reason = '') {
    this.onclose?.({ code, reason })
  }
}

const last = () => FakeSocket.all.at(-1)!
const READY = { type: 'ready', session_id: 'panel-web:1', agent: 'main', conversation: { id: 'c1', agent: 'main' } }

function session() {
  const s = createChatSession({
    conversationId: 'c1',
    token: 'secreto',
    url: 'ws://gw/ws/stream',
    WebSocketImpl: FakeSocket as unknown as typeof WebSocket,
  })
  s.start()
  last().open()
  last().receive(READY)
  return s
}

describe('createChatSession', () => {
  beforeEach(() => {
    FakeSocket.all = []
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  it('entra con el token de administrador en su conversación, sin pedir agente', () => {
    const s = session()
    expect(last().sent[0]).toEqual({ admin_token: 'secreto', input_mode: 'text', output_mode: ['text'], conversation: 'c1' })
    expect(s.state.connection).toBe('ready')
    expect(s.state.agent).toBe('main')
  })

  it('manda lo escrito y sigue la respuesta token a token', () => {
    const s = session()
    expect(s.send('  Hola  ')).toBe(true)
    expect(last().sent.at(-1)).toEqual({ type: 'send', text: 'Hola' })
    last().receive({ type: 'turn_start', turn_id: 't-1', turn_seq: 1, conversation: 'c1', origin: 'user' })
    last().receive({ type: 'token', turn_id: 't-1', text: '¡Hola!' })
    expect(s.state.log.turnOpen).toBe(true)
    s.cancel()
    expect(last().sent.at(-1)).toEqual({ type: 'cancel' })
    last().receive({ type: 'turn_end', turn_id: 't-1', reason: 'cancelled' })
    expect(s.state.log.lines.map((l) => [l.role, l.text, l.reason])).toEqual([
      ['user', 'Hola', 'done'],
      ['assistant', '¡Hola!', 'cancelled'],
    ])
  })

  it('un push llega como Fogar hablando primero', () => {
    const s = session()
    last().receive({ type: 'turn_start', turn_id: 't-1', turn_seq: 1, conversation: 'c1', origin: 'push' })
    expect(s.state.log.lines[0]).toMatchObject({ role: 'assistant', origin: 'push', state: 'streaming' })
  })

  it('«abierta en otro sitio» no reconecta sola; «seguir aquí» sí', () => {
    const s = session()
    last().receive({ type: 'turn_start', turn_id: 't-1', turn_seq: 1, conversation: 'c1', origin: 'user' })
    last().receive({ type: 'turn_end', turn_id: 't-1', reason: 'closed' })
    last().serverClose(1008, 'conversation opened elsewhere')
    expect(s.state.connection).toBe('elsewhere')
    vi.advanceTimersByTime(120_000)
    expect(FakeSocket.all).toHaveLength(1)
    expect(s.send('hola')).toBe(false)

    s.retake()
    expect(FakeSocket.all).toHaveLength(2)
    expect(s.state.log.lines).toEqual([]) // se vuelve a leer lo dicho
    last().open()
    last().receive(READY)
    expect(s.state.connection).toBe('ready')
  })

  it('si la conversación ya está abierta en otra pestaña, no la quita al entrar', () => {
    const s = createChatSession({ conversationId: 'c1', token: 't', WebSocketImpl: FakeSocket as unknown as typeof WebSocket, url: 'ws://x' })
    s.start(true)
    expect(s.state.connection).toBe('elsewhere')
    expect(FakeSocket.all).toHaveLength(0)
  })

  it('tras una caída reintenta solo, con espera, y cierra el turno que quedó abierto', () => {
    const s = session()
    last().receive({ type: 'turn_start', turn_id: 't-1', turn_seq: 1, conversation: 'c1', origin: 'user' })
    last().serverClose(1012)
    expect(s.state.connection).toBe('reconnecting')
    expect(s.state.log.lines[0]).toMatchObject({ state: 'ended', reason: 'closed' })
    vi.advanceTimersByTime(999)
    expect(FakeSocket.all).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeSocket.all).toHaveLength(2)
    last().open()
    expect(last().sent[0]).toMatchObject({ conversation: 'c1' }) // la misma conversación
    last().receive(READY)
    expect(s.state.connection).toBe('ready')
    expect(s.state.failures).toBe(0)
  })

  it('cerrada por inactividad, se reabre al escribir y manda el mensaje al entrar', () => {
    const s = session()
    last().serverClose(1000)
    expect(s.state.connection).toBe('idle')
    vi.advanceTimersByTime(60_000)
    expect(FakeSocket.all).toHaveLength(1)
    expect(s.send('¿Sigues ahí?')).toBe(true)
    expect(FakeSocket.all).toHaveLength(2)
    last().open()
    expect(last().sent).toHaveLength(1) // solo el handshake hasta el ready
    last().receive(READY)
    expect(last().sent.at(-1)).toEqual({ type: 'send', text: '¿Sigues ahí?' })
  })

  it('para con un 1008 que no se arregla reconectando y dice por qué', () => {
    const s = session()
    last().serverClose(1008, 'conversation not found')
    expect(s.state.connection).toBe('stopped')
    expect(s.state.notice).toMatch(/ya no existe/)
    vi.advanceTimersByTime(60_000)
    expect(FakeSocket.all).toHaveLength(1)
  })

  it('sigue los avisos de OpenClaw y al salir cierra sin reintentar', () => {
    const s = session()
    last().receive({ type: 'status', service: 'orchestrator', state: 'reconnecting' })
    expect(s.state.orchestratorDown).toBe(true)
    last().receive({ type: 'status', service: 'orchestrator', state: 'restored' })
    expect(s.state.orchestratorDown).toBe(false)
    const socket = last()
    s.dispose()
    expect(socket.closedWith).toBe(1000)
    socket.serverClose(1000)
    vi.advanceTimersByTime(60_000)
    expect(FakeSocket.all).toHaveLength(1)
  })
})
