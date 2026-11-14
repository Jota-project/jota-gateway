import type { OpenClawAccess } from '@/api/types'

// Con qué acceso está el gateway en OpenClaw (M19), para Estado. Las frases siguen a las de la CLI
// (src/fogar_gateway/cli/openclaw_access.py) para que `fogar-gw status` y el panel digan lo mismo.
// Nunca hay secretos aquí: ni el token ni la clave privada llegan al panel; el deviceId es público.

export const SCOPES = [
  { scope: 'operator.read', texto: 'Leer' },
  { scope: 'operator.write', texto: 'Escribir' },
  { scope: 'operator.admin', texto: 'Administrar' },
] as const

export const AUTH_METHOD: Record<string, string> = {
  shared_token: 'Token compartido',
  device_token: 'Token de dispositivo',
}

const IDENTITY_ERROR: Record<string, string> = {
  identity_file_invalid: 'el fichero de identidad del gateway no vale o no se puede crear',
  challenge_without_nonce: 'este OpenClaw no manda nonce en el reto y no hay nada que firmar',
}

/** El comando que aprueba el gateway en la máquina de OpenClaw (como `approve_command` del gateway). */
export const approveCommand = (requestId: string | null | undefined) => `openclaw devices approve ${requestId || '--latest'}`

export type AccessTone = 'ok' | 'warn' | 'off'

export interface AccessView {
  tono: AccessTone
  /** La frase de arriba. */
  titulo: string
  detalle: string | null
  /** Cada permiso: concedido o no; `null` si no se saben (sin conexión, o OpenClaw no los anuncia). */
  permisos: { texto: string; concedido: boolean }[] | null
  /** Otros permisos que OpenClaw concedió y la web no conoce. */
  otros: string[]
  metodo: string | null
  /** Solo con el emparejamiento pendiente: el comando que lo aprueba. */
  comando: string | null
}

/** Lo que enseña Estado del acceso; `null` si el gateway no da el dato (una versión anterior a M19). */
export function describeAccess(access: Partial<OpenClawAccess> | undefined): AccessView | null {
  if (!access || access.pairing === undefined) return null
  const scopes = access.scopes ?? null
  const permisos = scopes ? SCOPES.map((s) => ({ texto: s.texto, concedido: scopes.includes(s.scope) })) : null
  const otros = scopes ? scopes.filter((s) => !SCOPES.some((k) => k.scope === s)) : []
  const metodo = access.auth_method ? (AUTH_METHOD[access.auth_method] ?? access.auth_method) : null
  const base = { permisos, otros, metodo, comando: null }

  switch (access.pairing) {
    case 'pending':
      return {
        ...base,
        tono: 'warn',
        titulo: 'Falta aprobar el gateway en OpenClaw',
        detalle: 'OpenClaw espera que apruebes este gateway como dispositivo. Ejecuta esto en la máquina de OpenClaw; el gateway se conecta solo al aprobarlo.',
        comando: approveCommand(access.pairing_request_id),
      }
    case 'identity_required':
      return {
        ...base,
        tono: 'warn',
        titulo: 'OpenClaw pide una identidad que el gateway no puede mandar',
        detalle: `Motivo: ${IDENTITY_ERROR[access.identity_error ?? ''] ?? 'no hay identidad que mandar'}. Revísalo con fogar-gw doctor.`,
      }
    case 'paired':
    case 'not_required': {
      const como = access.pairing === 'paired' ? 'Emparejado como dispositivo' : 'Sin emparejar: no hace falta'
      if (access.admin) return { ...base, tono: 'ok', titulo: 'Con permisos de administración', detalle: como }
      if (!scopes) return { ...base, tono: 'ok', titulo: 'Conectado, con permisos desconocidos', detalle: `${como}. Este OpenClaw no los anuncia.` }
      return { ...base, tono: 'ok', titulo: 'Sin permisos de administración', detalle: `${como}. Hoy no hacen falta para hablar.` }
    }
    default:
      return { ...base, tono: 'off', titulo: 'Sin conexión con OpenClaw ahora', detalle: 'El acceso se ve en cuanto conecte.' }
  }
}
