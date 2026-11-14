import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'
import { listConversations } from '@/api/admin'
import type { Conversation } from '@/api/types'

/** Las conversaciones de toda la casa (sin texto: el título viene de OpenClaw). Las comparten la lista y cada conversación. */
export const useConversationsStore = defineStore('conversations', () => {
  const items = shallowRef<Conversation[]>([])
  const loaded = ref(false)
  const error = ref<string | null>(null)

  async function refresh(): Promise<void> {
    try {
      items.value = await listConversations()
      error.value = null
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'No llego al gateway'
    } finally {
      loaded.value = true
    }
  }

  /** Una recién creada entra en la lista sin esperar a la siguiente lectura. */
  function add(c: Conversation): void {
    items.value = [c, ...items.value.filter((x) => x.id !== c.id)]
  }

  const byId = computed(() => new Map(items.value.map((c) => [c.id, c])))

  return { items, loaded, error, byId, refresh, add }
})
