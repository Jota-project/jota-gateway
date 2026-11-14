import { reactive } from 'vue'
import { backoffMs, ChatLog, closeAction, stopText, type ServerMessage } from './chat'

// Una conversación del panel en vivo: abre /ws/stream con el token de administrador (entra como el
// cliente «Panel web») en esa conversación, manda texto y sigue la respuesta token a token.
// El token va solo en el handshake: nunca se registra ni se enseña.

export type Connection =
  | 'connecting' // abriendo, hasta el `ready`
  | 'ready'
  | 'reconnecting' // se cayó: reintenta solo, con espera creciente
  | 'idle' // cerrada sin error (inactividad): se reabre al escribir
  | 'elsewhere' // otra pestaña o ventana abrió la conversación: solo vuelve si lo pide la persona
  | 'stopped' // no tiene arreglo reconectando (ver `notice`)

export interface ChatSessionOptions {
  conversationId: string
  token: string
  /** Para las pruebas. */
  WebSocketImpl?: typeof WebSocket
  url?: string
}

export interface ChatSessionState {
  connection: Connection
  /** El agente de la conversación, de `ready`. */
  agent: string | null
  /** OpenClaw se está reconectando (avisos `status` de la sesión). */
  orchestratorDown: boolean
  /** Por qué se paró (`stopped`). */
  notice: string | null
  /** Reintentos seguidos sin llegar a `ready`. */
  failures: number
  log: ChatLog
}

function defaultUrl(): string {
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/stream`
}

export function createChatSession(options: ChatSessionOptions) {
  const Impl = options.WebSocketImpl ?? WebSocket
  const state = reactive<ChatSessionState>({
    connection: 'connecting',
    agent: null,
    orchestratorDown: false,
    notice: null,
    failures: 0,
    log: new ChatLog(options.conversationId),
  })
  let ws: WebSocket | null = null
  let retry: ReturnType<typeof setTimeout> | null = null
  // Escrito con la conversación dormida: sale al llegar `ready` (su línea, por si no sale).
  let pending: { text: string; key: string } | null = null
  let disposed = false

  function connect(): void {
    if (disposed) return
    if (retry) clearTimeout(retry)
    retry = null
    if (state.connection !== 'reconnecting') state.connection = 'connecting'
    state.notice = null
    const socket = new Impl(options.url ?? defaultUrl())
    ws = socket
    socket.onopen = () => {
      socket.send(
        JSON.stringify({
          admin_token: options.token,
          input_mode: 'text',
          // `status`: el progreso del turno (`turn_progress`); llegan también los `pipeline_event`, que el chat ignora.
          output_mode: ['text', 'status'],
          conversation: options.conversationId,
        }),
      )
    }
    socket.onmessage = (event) => {
      if (ws !== socket || typeof event.data !== 'string') return
      let msg: ServerMessage
      try {
        msg = JSON.parse(event.data) as ServerMessage
      } catch {
        return
      }
      onMessage(msg)
    }
    socket.onclose = (event) => {
      if (ws !== socket) return
      ws = null
      onClose(event.code)
    }
  }

  function onMessage(msg: ServerMessage): void {
    if (msg.type === 'ready') {
      const m = msg as Extract<ServerMessage, { type: 'ready' }>
      state.connection = 'ready'
      state.failures = 0
      // Un `restored` solo llega a quien estaba conectado al volver OpenClaw: al entrar, se parte de cero.
      state.orchestratorDown = false
      state.agent = m.conversation?.agent ?? m.agent
      if (pending != null) {
        ws?.send(JSON.stringify({ type: 'send', text: pending.text }))
        pending = null
      }
      return
    }
    if (msg.type === 'status' && msg.service === 'orchestrator') {
      state.orchestratorDown = msg.state !== 'restored'
      return
    }
    state.log.apply(msg)
  }

  function onClose(code: number): void {
    state.log.closeOpenTurns()
    if (disposed) return
    const action = closeAction(code)
    if (action === 'retry') {
      state.connection = 'reconnecting'
      state.failures += 1
      retry = setTimeout(connect, backoffMs(state.failures))
      return
    }
    if (pending) state.log.markUnsent(pending.key) // no se envió: que se vea
    pending = null
    if (action === 'stop') state.notice = stopText(code)
    state.connection = action === 'stop' ? 'stopped' : action
  }

  /** Manda un mensaje. Con la conversación dormida, la reabre y lo manda al entrar. */
  function send(text: string): boolean {
    const clean = text.trim()
    if (!clean) return false
    if (state.connection === 'ready' && ws) {
      ws.send(JSON.stringify({ type: 'send', text: clean }))
      state.log.addUser(clean)
    } else if (state.connection === 'idle') {
      pending = { text: clean, key: state.log.addUser(clean) }
      connect()
    } else return false
    return true
  }

  function cancel(): void {
    if (state.connection === 'ready' && state.log.turnOpen) ws?.send(JSON.stringify({ type: 'cancel' }))
  }

  /**
   * Empieza. Si la conversación estaba abierta en otra pestaña, esa recibe el 4102 («conversation
   * opened elsewhere») y se queda con su aviso (no vuelve sola): nunca se echan una a otra.
   */
  function start(): void {
    connect()
  }

  /** Lo dicho se ha vuelto a leer de OpenClaw: fuera lo terminado aquí, que ya viene en ello. */
  function historyReloaded(): void {
    state.log.keepStreaming()
  }

  /** «Seguir aquí» (o reintentar tras un `stopped`): vuelve a abrir la conversación, que se cierra en el otro sitio. */
  function retake(): void {
    if (ws || disposed) return
    state.log = new ChatLog(options.conversationId)
    state.failures = 0
    state.connection = 'connecting'
    connect()
  }

  function dispose(): void {
    disposed = true
    if (retry) clearTimeout(retry)
    const socket = ws
    ws = null
    socket?.close(1000)
  }

  return { state, start, send, cancel, retake, historyReloaded, dispose }
}

export type ChatSession = ReturnType<typeof createChatSession>
