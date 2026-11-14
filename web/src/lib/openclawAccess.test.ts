import { describe, expect, it } from 'vitest'
import type { OpenClawAccess } from '@/api/types'
import { describeAccess } from './openclawAccess'

const access = (over: Partial<OpenClawAccess>): OpenClawAccess => ({
  scopes: ['operator.read', 'operator.write', 'operator.admin'],
  admin: true,
  auth_method: 'device_token',
  pairing: 'paired',
  pairing_request_id: null,
  identity_error: null,
  device_id: 'abc',
  ...over,
})

describe('describeAccess', () => {
  it('cuenta permisos y método', () => {
    const v = describeAccess(access({}))!
    expect(v.tono).toBe('ok')
    expect(v.titulo).toBe('Con permisos de administración')
    expect(v.permisos).toEqual([
      { texto: 'Leer', concedido: true },
      { texto: 'Escribir', concedido: true },
      { texto: 'Administrar', concedido: true },
    ])
    expect(v.metodo).toBe('Token de dispositivo')
    expect(v.comando).toBeNull()
  })

  it('sin admin, con el token compartido', () => {
    const v = describeAccess(
      access({ scopes: ['operator.read', 'operator.write', 'operator.pairing'], admin: false, auth_method: 'shared_token', pairing: 'not_required' }),
    )!
    expect(v.titulo).toBe('Sin permisos de administración')
    expect(v.permisos?.map((p) => p.concedido)).toEqual([true, true, false])
    expect(v.otros).toEqual(['operator.pairing'])
    expect(v.metodo).toBe('Token compartido')
  })

  it('pendiente de aprobar: da el comando con el id de la petición', () => {
    const v = describeAccess(access({ pairing: 'pending', pairing_request_id: 'req-42', scopes: null, admin: false }))!
    expect(v.tono).toBe('warn')
    expect(v.comando).toBe('openclaw devices approve req-42')
    expect(v.permisos).toBeNull()
    expect(describeAccess(access({ pairing: 'pending', pairing_request_id: null }))!.comando).toBe('openclaw devices approve --latest')
  })

  it('sin conexión ahora, y un gateway anterior a M19 que no da el dato', () => {
    expect(describeAccess(access({ pairing: 'unknown', scopes: null, auth_method: null }))!.tono).toBe('off')
    expect(describeAccess({})).toBeNull()
  })
})
