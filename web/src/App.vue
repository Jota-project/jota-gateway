<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AmbientLights from './components/AmbientLights.vue'
import AppRail from './components/AppRail.vue'
import { useLiveStore } from './stores/live'
import { useSessionStore } from './stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const live = useLiveStore()

const isPublic = computed(() => Boolean(route.meta.public))

// El estado vivo arranca en cuanto hay token y se para al salir.
watch(
  () => session.token,
  (token) => {
    if (token) live.start(() => void router.replace('/conectar'))
    else live.halt()
  },
  { immediate: true },
)

const warn = computed(() => live.connection === 'offline' || !live.allHealthy)
</script>

<template>
  <AmbientLights :warn="warn" />
  <RouterView v-if="isPublic" />
  <div v-else class="shell">
    <AppRail />
    <main class="content">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.shell {
  position: relative;
  min-height: 100vh;
  display: flex;
  flex-wrap: wrap;
}

.content {
  position: relative;
  z-index: 1;
  flex: 999 1 560px;
  min-width: 0;
  padding: 36px 48px 56px;
}

@media (max-width: 760px) {
  .content {
    padding: 16px;
  }
}
</style>
