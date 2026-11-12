import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'
import { listClients } from '@/api/admin'
import type { ClientResponse } from '@/api/types'

/** Los clientes del gateway (sin claves: solo `key_fp`). Los comparten la barra lateral y Clientes. */
export const useClientsStore = defineStore('clients', () => {
  const items = shallowRef<ClientResponse[]>([])
  const loaded = ref(false)
  const error = ref<string | null>(null)

  async function refresh(): Promise<void> {
    try {
      items.value = await listClients()
      error.value = null
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'No llego al gateway'
    } finally {
      loaded.value = true
    }
  }

  const byId = computed(() => new Map(items.value.map((c) => [c.id, c])))

  return { items, loaded, error, byId, refresh }
})
