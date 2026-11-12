import { useSessionStore } from '@/stores/session'

export type ApiErrorKind = 'unauthorized' | 'not-configured' | 'not-found' | 'conflict' | 'invalid' | 'network' | 'server'

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function kindFor(status: number): ApiErrorKind {
  if (status === 401) return 'unauthorized'
  if (status === 503) return 'not-configured' // /admin sin ADMIN_TOKEN en el gateway
  if (status === 404) return 'not-found'
  if (status === 409) return 'conflict'
  if (status === 422) return 'invalid'
  return 'server'
}

export interface RequestOptions extends RequestInit {
  /** Token que sustituye al guardado (Conectar lo comprueba antes de guardarlo). */
  token?: string
  timeoutMs?: number
  /** Códigos que traen un cuerpo válido aunque no sean 2xx (`/ready` responde 503 si cae OpenClaw). */
  okStatuses?: number[]
}

/** Petición a la API del gateway (mismo origen) con X-Admin-Token. */
export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { token, timeoutMs = 10_000, okStatuses = [], headers, ...init } = options
  const adminToken = token ?? useSessionStore().token
  const timeout = AbortSignal.timeout(timeoutMs)
  const signal = init.signal ? AbortSignal.any([init.signal, timeout]) : timeout

  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      signal,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(adminToken ? { 'X-Admin-Token': adminToken } : {}),
        ...headers,
      },
    })
  } catch {
    throw new ApiError('network', 0, 'No llego al gateway')
  }

  if (!response.ok && !okStatuses.includes(response.status)) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // cuerpo sin JSON
    }
    throw new ApiError(kindFor(response.status), response.status, detail)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
