<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BarraLateral from './components/BarraLateral.vue'
import FgAvisos from './components/FgAvisos.vue'
import { useClientsStore } from './stores/clients'
import { useLiveStore } from './stores/live'
import { useSessionStore } from './stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const live = useLiveStore()
const clients = useClientsStore()

const isPublic = computed(() => Boolean(route.meta.public))
const nombre = ref<string>()

// El estado vivo arranca en cuanto hay token y se para al salir.
watch(
  () => session.token,
  (token) => {
    if (token) {
      live.start(() => void router.replace('/conectar'))
      void clients.refresh()
    } else live.halt()
  },
  { immediate: true },
)

// Nombre del gateway (público, sin token): para saber en qué casa estás.
onMounted(async () => {
  try {
    const r = await fetch('/.well-known/fogar-gateway')
    if (r.ok) nombre.value = ((await r.json()) as { name?: string }).name
  } catch {
    // sin nombre: la barra no lo enseña
  }
})
</script>

<template>
  <RouterView v-if="isPublic" />
  <div v-else class="armazon">
    <BarraLateral :nombre="nombre" :version="live.state?.gateway.version" />
    <main class="contenido">
      <RouterView />
    </main>
  </div>
  <FgAvisos />
</template>

<style scoped>
.armazon {
  min-height: 100vh;
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
}
.contenido {
  flex: 1 1 560px;
  min-width: 0;
  max-width: 1240px;
  padding: 36px 40px 64px;
}
@media (max-width: 760px) {
  .armazon {
    flex-direction: column;
    flex-wrap: nowrap;
  }
  .contenido {
    flex: 1 1 auto;
    width: 100%;
    padding: 20px 16px 96px;
  }
}
</style>
