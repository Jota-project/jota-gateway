import { label, TURN_ERROR } from './labels'
import { reconnectClass, type Reconnect } from './closeCodes'

// El chat del panel por el WebSocket (docs/client-protocol.md): lo que llega, cómo queda en pantalla y
// qué hacer cuando se cierra. Sin Vue ni sockets: lo usa `chatSession.ts` y se prueba solo.

/** Lo que el gateway manda en una sesión de texto (`output_mode: ["text"]`) que el chat usa. */
export type ServerMessage =
  | { type: 'ready'; session_id: string; agent: string; conversation?: { id: string; agent: string } }
  | { type: 'turn_start'; turn_id: string; turn_seq: number; conversation?: string | null; origin?: 'user' | 'push' }
  | { type: 'token'; turn_id: string; text: string }
  | { type: 'turn_end'; turn_id: string; reason?: string }
  | { type: 'error'; code: string; message: string; fatal: boolean; turn_id?: string }
  | { type: 'status'; service: string; state: string }
  | { type: string; [key: string]: unknown }

export interface ChatLine {
  key: string
  role: 'user' | 'assistant'
  text: string
  /** ISO; `null` si no se sabe. */
  at: string | null
  /** Solo en las de Fogar: `push` es Fogar hablando sin que le preguntaran. */
  origin?: 'user' | 'push'
  /** `streaming` mientras llegan tokens; `ended` con su `turn_end` (motivo en `reason`). */
  state: 'streaming' | 'ended'
  reason?: string
  /** El `message` del `error` de su turno (código estable, §9). */
  error?: string
  /** Un push de otra conversación del panel (el gateway lo entrega en la sesión más reciente). */
  otherConversation?: string | null
  /** Solo en las tuyas: escrita con la conversación dormida y no llegó a salir (falló la reconexión). */
  unsent?: boolean
}

/** Las líneas de la conversación en vivo, en el orden en que llegan (el orden total del gateway). */
export class ChatLog {
  lines: ChatLine[] = []
  private seq = 0

  constructor(private conversationId: string) {}

  /** Añade lo que escribiste; devuelve su clave (para marcarla si no llega a salir). */
  addUser(text: string, at = new Date().toISOString()): string {
    const key = `u-${++this.seq}`
    this.lines.push({ key, role: 'user', text, at, state: 'ended', reason: 'done' })
    return key
  }

  markUnsent(key: string): void {
    const line = this.lines.find((l) => l.key === key)
    if (line) line.unsent = true
  }

  /**
   * Se ha vuelto a leer lo dicho de OpenClaw, que ya trae lo terminado: solo se queda lo que sigue
   * llegando (un turno a medias), para no enseñar nada dos veces.
   */
  keepStreaming(): void {
    this.lines = this.lines.filter((l) => l.state === 'streaming')
  }

  private turn(turnId: string): ChatLine | undefined {
    for (let i = this.lines.length - 1; i >= 0; i--) {
      const line = this.lines[i]!
      if (line.role === 'assistant' && line.key === `t-${turnId}`) return line
    }
    return undefined
  }

  /** Aplica un mensaje del gateway. Devuelve `true` si cambió algo de la conversación. */
  apply(msg: ServerMessage, at = new Date().toISOString()): boolean {
    switch (msg.type) {
      case 'turn_start': {
        const m = msg as Extract<ServerMessage, { type: 'turn_start' }>
        // Un turn_id se repite tras reconectar (t-1, t-2… por sesión): el viejo ya terminó.
        const old = this.turn(m.turn_id)
        if (old) old.key = `${old.key}-${++this.seq}`
        const other = m.conversation && m.conversation !== this.conversationId ? m.conversation : null
        this.lines.push({
          key: `t-${m.turn_id}`,
          role: 'assistant',
          text: '',
          at,
          origin: m.origin === 'push' ? 'push' : 'user',
          state: 'streaming',
          ...(other ? { otherConversation: other } : {}),
        })
        return true
      }
      case 'token': {
        const m = msg as Extract<ServerMessage, { type: 'token' }>
        const line = this.turn(m.turn_id)
        if (!line || line.state !== 'streaming') return false
        line.text += m.text
        return true
      }
      case 'error': {
        const m = msg as Extract<ServerMessage, { type: 'error' }>
        const line = m.turn_id ? this.turn(m.turn_id) : undefined
        if (!line) return false
        line.error = m.message
        return true
      }
      case 'turn_end': {
        const m = msg as Extract<ServerMessage, { type: 'turn_end' }>
        const line = this.turn(m.turn_id)
        if (!line || line.state !== 'streaming') return false
        line.state = 'ended'
        line.reason = m.reason ?? 'done'
        return true
      }
      default:
        return false
    }
  }

  /** El socket se cerró: lo que seguía abierto ya no va a terminar (su `turn_end` no llegará). */
  closeOpenTurns(): void {
    for (const line of this.lines) {
      if (line.state === 'streaming') {
        line.state = 'ended'
        line.reason = 'closed'
      }
    }
  }

  get turnOpen(): boolean {
    return this.lines.some((line) => line.state === 'streaming')
  }
}

const REASON: Record<string, string> = {
  cancelled: 'La paraste aquí.',
  interrupted: 'Se interrumpió: hablaste encima.',
  replaced: 'Se cortó: la sustituyó tu siguiente mensaje.',
  timeout: 'Se cortó: OpenClaw dejó de responder.',
  closed: 'Se cortó: se cerró la conexión.',
}

/** Por qué no terminó una respuesta, en una frase; `null` si terminó bien. Un motivo nuevo cuenta como error. */
export function endReasonText(line: Pick<ChatLine, 'reason' | 'error'>): string | null {
  const reason = line.reason ?? 'done'
  if (reason === 'done') return null
  if (REASON[reason]) return REASON[reason]
  return line.error ? `Falló: ${label(TURN_ERROR, line.error).toLowerCase()}.` : 'Falló.'
}

/**
 * Qué hacer al cerrarse el socket, según la clase del código (`closeCodes.ts`, nunca el texto):
 * - `elsewhere` (`never_auto`, 4102): otra sesión abrió esta conversación. Nunca se reconecta solo
 *   (§9): las dos se echarían una a otra sin fin; solo si lo pide la persona.
 * - `stop` (`fix`: el resto de 4000–4899 y 1008): no tiene arreglo reconectando (conversación que ya
 *   no existe, panel desactivado, acceso retirado…). Lo decide la persona, con «Reintentar».
 * - `idle` (`on_input`, 1000): inactividad de 5 min: se reabre al escribir, no antes.
 * - `retry` (`backoff`): caída (1006), fallo del gateway u OpenClaw caído (1011), reinicio (1012),
 *   cliente lento (1013), 49xx (handshake a destiempo…).
 */
export type CloseAction = 'elsewhere' | 'stop' | 'idle' | 'retry'

const ACTION: Record<Reconnect, CloseAction> = {
  never_auto: 'elsewhere',
  fix: 'stop',
  on_input: 'idle',
  backoff: 'retry',
}

export function closeAction(code: number): CloseAction {
  return ACTION[reconnectClass(code)]
}

/** Frases de los cierres que paran el chat (`stop`) y tienen una explicación propia. */
const STOP_TEXT: Partial<Record<number, string>> = {
  4010: 'El panel no puede escribir: su cliente («Panel web») está desactivado. Actívalo en Clientes.',
  4020: 'El agente de esta conversación no está permitido para el panel.',
  4021: 'El agente de esta conversación no existe en OpenClaw.',
  4100: 'Esta conversación ya no existe en el gateway.',
  4101: 'El agente no coincide con el de la conversación.',
  4200: 'Se retiró el acceso del panel: su cliente («Panel web») se desactivó, se borró o cambió su clave.',
}

/** La frase para un cierre que para el chat (`stop`). */
export function stopText(code: number): string {
  return STOP_TEXT[code] ?? 'El gateway no aceptó la conexión del chat.'
}

/** Espera antes de reintentar: 1 s, 2 s, 4 s… hasta 30 s. */
export function backoffMs(failures: number): number {
  return Math.min(30_000, 1000 * 2 ** Math.max(0, failures - 1))
}
