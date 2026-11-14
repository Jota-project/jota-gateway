// Códigos de cierre de /ws/stream y qué hacer con cada uno (docs/client-protocol.md §13, «Códigos de
// cierre»). Es una copia de src/fogar_gateway/models/close_codes.py: tests/unit/test_close_codes.py
// la compara entrada a entrada, así que un código nuevo o cambiado en el gateway rompe ese test hasta
// que se copia aquí. El código es el contrato: el `reason` es texto para personas y nunca se compara.

/**
 * - `backoff`: reconectar solo, con espera creciente.
 * - `on_input`: no es un fallo; se reabre cuando haga falta (en el panel, al escribir).
 * - `never_auto`: solo si lo pide la persona; reconectar solo echaría a otra sesión.
 * - `fix`: no se arregla reconectando; hay que cambiar algo (la clave, el panel activo, el agente…).
 */
export type Reconnect = 'backoff' | 'on_input' | 'never_auto' | 'fix'

export const CLOSE_CODES = {
  1000: 'on_input', // fin normal: inactividad, silencio
  1011: 'backoff', // fallo interno al montar o con la sesión abierta; OpenClaw caído al entrar
  1013: 'backoff', // cliente lento
  4000: 'fix', // handshake inválido
  4001: 'fix', // handshake no es texto
  4002: 'fix', // handshake demasiado grande
  4010: 'fix', // credencial inválida o inactiva
  4020: 'fix', // agente no permitido
  4021: 'fix', // agente no disponible
  4100: 'fix', // conversación no encontrada
  4101: 'fix', // agente distinto del de la conversación
  4102: 'never_auto', // conversación abierta en otra sesión
  4200: 'fix', // credencial revocada
  4900: 'backoff', // handshake a destiempo
} as const satisfies Record<number, Reconnect>

/** La clase de un código: la de la tabla o, si no está, la de su rango (§13). */
export function reconnectClass(code: number): Reconnect {
  const known = (CLOSE_CODES as Record<number, Reconnect>)[code]
  if (known) return known
  if (code >= 4900 && code <= 4999) return 'backoff' // 49xx: pasajeros de aplicación
  if (code >= 4000 && code <= 4899) return 'fix' // 40xx–48xx: no se reconecta solo
  if (code === 1008) return 'fix' // violación de política genérica: no insistir
  return 'backoff' // transporte: 1006 caída, 1012 reinicio, otros
}
