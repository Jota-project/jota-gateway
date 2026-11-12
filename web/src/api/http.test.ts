import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { api, ApiError } from './http'

function reply(status: number, body: unknown): void {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status })))
}

describe('api', () => {
  setActivePinia(createPinia())
  afterEach(() => vi.unstubAllGlobals())

  it('un 503 de /admin es que falta ADMIN_TOKEN', async () => {
    reply(503, { detail: 'admin disabled' })
    await expect(api('/admin/state')).rejects.toMatchObject({ kind: 'not-configured', status: 503 })
  })

  it('/ready con OpenClaw caído responde 503 con cuerpo: se lee, no es un error', async () => {
    const body = { status: 'unavailable', services: { orchestrator: 'unavailable', transcriber: 'ok', tts: 'ok' } }
    reply(503, body)
    await expect(api('/ready', { okStatuses: [503] })).resolves.toEqual(body)
  })

  it('otros fallos siguen siendo errores aunque se acepte el 503', async () => {
    reply(500, {})
    await expect(api('/ready', { okStatuses: [503] })).rejects.toBeInstanceOf(ApiError)
  })
})
