import { api } from './http'
import type { ReadyResponse, ServiceDetail } from './types'

// Una función por endpoint que usa la web.

export const getOrchestratorStatus = () => api<ServiceDetail>('/admin/orchestrators/openclaw/status')
/** Hace un sondeo real del transcriptor: solo al entrar en Estado y al comprobar servicios. */
export const getTranscriberStatus = () => api<ServiceDetail>('/admin/transcriber/status')
export const getTtsStatus = () => api<ServiceDetail>('/admin/tts/status')
export const reconnectOrchestrator = () => api<{ accepted: boolean }>('/admin/orchestrators/openclaw/reconnect', { method: 'POST' })
/** Sondea los tres servicios de verdad (puede tardar): solo al pulsar «Comprobar servicios». */
export const getReady = () => api<ReadyResponse>('/ready', { okStatuses: [503], timeoutMs: 20_000 })
