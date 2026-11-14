import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createConversation, getConversationAgents, getConversationMessages, listConversations } from './admin'

function reply(status: number, body: unknown) {
  const fetch = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(body), { status }))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('conversaciones (M19)', () => {
  setActivePinia(createPinia())
  afterEach(() => vi.unstubAllGlobals())

  it('lista y lee lo dicho de una, con su límite', async () => {
    let fetch = reply(200, [])
    await listConversations()
    expect(fetch.mock.calls[0]![0]).toBe('/admin/conversations')
    fetch = reply(200, { conversation_id: 'a b', messages: [], has_more: false })
    await getConversationMessages('a b', 1000)
    expect(fetch.mock.calls[0]![0]).toBe('/admin/conversations/a%20b/messages?limit=1000')
  })

  it('crea con el agente elegido, o sin agente para el de por defecto', async () => {
    const fetch = reply(201, { id: 'n' })
    await createConversation('casa')
    await createConversation(null)
    expect(fetch.mock.calls.map(([, init]) => [init?.method, init?.body])).toEqual([
      ['POST', '{"agent":"casa"}'],
      ['POST', '{}'],
    ])
  })

  it('los errores llegan con su código estable', async () => {
    reply(503, { detail: 'orchestrator_unavailable' })
    await expect(getConversationMessages('x')).rejects.toMatchObject({ status: 503, message: 'orchestrator_unavailable' })
    reply(409, { detail: 'panel_client_inactive' })
    await expect(getConversationAgents()).rejects.toMatchObject({ kind: 'conflict', message: 'panel_client_inactive' })
  })
})
