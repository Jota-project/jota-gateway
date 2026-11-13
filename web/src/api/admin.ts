import { api } from './http'
import type {
  ClientInput,
  ClientResponse,
  ClientWithKey,
  PairingOpened,
  PairingSummary,
  PairingTemplate,
  ReadyResponse,
  ServiceDetail,
  SessionDetail,
  SessionList,
} from './types'

// Una función por endpoint que usa la web.

export const getOrchestratorStatus = () => api<ServiceDetail>('/admin/orchestrators/openclaw/status')
/** Hace un sondeo real del transcriptor: solo al entrar en Estado y al comprobar servicios. */
export const getTranscriberStatus = () => api<ServiceDetail>('/admin/transcriber/status')
export const getTtsStatus = () => api<ServiceDetail>('/admin/tts/status')
export const reconnectOrchestrator = () => api<{ accepted: boolean }>('/admin/orchestrators/openclaw/reconnect', { method: 'POST' })
/** Sondea los tres servicios de verdad (puede tardar): solo al pulsar «Comprobar servicios». */
export const getReady = () => api<ReadyResponse>('/ready', { okStatuses: [503], timeoutMs: 20_000 })

const json = (body: unknown) => JSON.stringify(body)
const enc = encodeURIComponent

export const listClients = () => api<ClientResponse[]>('/admin/clients')
export const getClient = (id: string) => api<ClientResponse>(`/admin/clients/${enc(id)}`)
/** La única respuesta, con la de rotar, que trae la clave: no se guarda en ningún store. */
export const createClient = (body: ClientInput) => api<ClientWithKey>('/admin/clients', { method: 'POST', body: json(body) })
export const updateClient = (id: string, body: ClientInput) =>
  api<ClientResponse>(`/admin/clients/${enc(id)}`, { method: 'PATCH', body: json(body) })
export const deleteClient = (id: string) => api<void>(`/admin/clients/${enc(id)}`, { method: 'DELETE' })
export const rotateClientKey = (id: string) => api<ClientWithKey>(`/admin/clients/${enc(id)}/rotate-key`, { method: 'POST' })

export const listSessions = () => api<SessionList>('/admin/sessions')
export const getSession = (id: string) => api<SessionDetail>(`/admin/sessions/${enc(id)}`)

/** El código solo llega aquí: no se guarda ni se registra. */
export const openPairing = (template: PairingTemplate = {}) =>
  api<PairingOpened>('/admin/pairing', { method: 'POST', body: json(template) })
export const getPairing = (id: string) => api<PairingSummary>(`/admin/pairing/${enc(id)}`)
export const cancelPairing = (id: string) => api<void>(`/admin/pairing/${enc(id)}`, { method: 'DELETE' })
