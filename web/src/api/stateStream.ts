import type { GatewayState } from './types'

/**
 * Trocea un flujo SSE en eventos y entrega el `data:` de cada uno. Aguanta trozos que
 * cortan un evento o una línea por la mitad y saltos de línea \r\n.
 */
export function createSseParser(onData: (data: string) => void): (chunk: string) => void {
  let buffer = ''
  return (chunk) => {
    buffer = (buffer + chunk).replace(/\r\n?/g, '\n')
    let end = buffer.indexOf('\n\n')
    while (end >= 0) {
      const event = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      const data = event
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).replace(/^ /, ''))
        .join('\n')
      if (data) onData(data)
      end = buffer.indexOf('\n\n')
    }
  }
}

export type StreamConnection = 'connecting' | 'live' | 'offline'

export interface StateStreamOptions {
  token: string
  onState: (state: GatewayState) => void
  onConnection: (connection: StreamConnection) => void
  onUnauthorized: () => void
}

const MAX_BACKOFF_MS = 30_000

/**
 * Sigue /admin/state/stream. `EventSource` no puede mandar X-Admin-Token, así que se lee con
 * fetch. El gateway corta cada flujo a los 25 s (STATE_STREAM_MAX_S): se reabre al momento.
 * Si falla, reintenta con espera creciente (1 s → 30 s). Con la pestaña oculta se para.
 * Devuelve la función que lo detiene.
 */
export function openStateStream(options: StateStreamOptions): () => void {
  let stopped = false
  let controller: AbortController | null = null
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let failures = 0

  async function connect(): Promise<void> {
    if (stopped || document.hidden) return
    const ctrl = new AbortController()
    controller = ctrl
    const decoder = new TextDecoder()
    try {
      const response = await fetch('/admin/state/stream', {
        headers: { 'X-Admin-Token': options.token, Accept: 'text/event-stream' },
        signal: ctrl.signal,
      })
      if (response.status === 401) {
        stopped = true
        options.onUnauthorized()
        return
      }
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`)

      const parse = createSseParser((data) => {
        failures = 0
        options.onConnection('live')
        options.onState(JSON.parse(data) as GatewayState)
      })
      const reader = response.body.getReader()
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        parse(decoder.decode(value, { stream: true }))
      }
      // Fin normal del flujo (25 s): se reabre sin esperar.
      void connect()
    } catch {
      if (stopped || ctrl.signal.aborted) return
      failures += 1
      options.onConnection(failures > 1 ? 'offline' : 'connecting')
      const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** (failures - 1))
      retryTimer = setTimeout(() => void connect(), delay)
    }
  }

  function onVisibility(): void {
    if (document.hidden) {
      controller?.abort()
      if (retryTimer) clearTimeout(retryTimer)
    } else {
      failures = 0
      void connect()
    }
  }

  document.addEventListener('visibilitychange', onVisibility)
  options.onConnection('connecting')
  void connect()

  return () => {
    stopped = true
    controller?.abort()
    if (retryTimer) clearTimeout(retryTimer)
    document.removeEventListener('visibilitychange', onVisibility)
  }
}
