import type { Conversation } from '@/api/types'
import { formatShortDate } from './format'

/** El cliente con el que entra el panel en el WebSocket (`admin_token`): sus conversaciones se escriben aquí. */
export const PANEL_CLIENT_ID = 'panel-web'

/** El título de OpenClaw o, si no lo tiene (o no responde), uno honesto con el día. */
export function conversationTitle(c: Pick<Conversation, 'title' | 'kind' | 'day' | 'created_at'>, now = new Date()): string {
  const title = c.title?.trim()
  if (title) return title
  if (c.kind === 'default' && c.day) return `Conversación del ${formatShortDate(c.day, now)}`
  return `Sin título · ${formatShortDate(c.created_at, now)}`
}

/** Qué clase de conversación es, solo cuando aporta: la del día (diaria) y las de antes de M19. */
export function conversationKind(kind: string): string | null {
  if (kind === 'default') return 'diaria'
  if (kind === 'legacy') return 'de antes'
  return null
}

/** Desde el panel solo se escribe en sus propias conversaciones; las de otros aparatos se leen (M19). */
export const canWrite = (c: Pick<Conversation, 'client_id'>) => c.client_id === PANEL_CLIENT_ID
