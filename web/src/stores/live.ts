import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'
import { openStateStream, type StreamConnection } from '@/api/stateStream'
import type { GatewayState } from '@/api/types'
import { useSessionStore } from './session'

/** Estado vivo del gateway: lo comparten la barra lateral y la pantalla Estado. */
export const useLiveStore = defineStore('live', () => {
  const state = shallowRef<GatewayState | null>(null)
  const connection = ref<StreamConnection>('connecting')
  let stop: (() => void) | null = null

  const allHealthy = computed(() => {
    const s = state.value?.services
    if (!s) return true
    return [s.orchestrator, s.stt, s.tts].every((service) => service.state === 'CONNECTED')
  })

  function start(onUnauthorized: () => void): void {
    const session = useSessionStore()
    if (stop || !session.token) return
    stop = openStateStream({
      token: session.token,
      onState: (next) => (state.value = next),
      onConnection: (next) => (connection.value = next),
      onUnauthorized: () => {
        stop = null
        session.signOut()
        onUnauthorized()
      },
    })
  }

  function halt(): void {
    stop?.()
    stop = null
    state.value = null
    connection.value = 'connecting'
  }

  return { state, connection, allHealthy, start, halt }
})
