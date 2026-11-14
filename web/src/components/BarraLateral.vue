<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useClientsStore } from '@/stores/clients'
import { useLiveStore } from '@/stores/live'
import { useSessionStore } from '@/stores/session'
import FgIcono, { type IconoNombre } from './FgIcono.vue'
import FgPalabra from './FgPalabra.vue'

// Navegación: barra lateral en escritorio; en el móvil, cabecera con menú y barra inferior.
// Sin barra superior y sin anunciar lo que aún no existe. El chat vive en Conversaciones (M19).
const props = defineProps<{ nombre?: string; version?: string }>()

const route = useRoute()
const router = useRouter()
const live = useLiveStore()
const clients = useClientsStore()
const session = useSessionStore()
const menu = ref(false)

const abiertas = computed(() => live.state?.sessions.length ?? null)
const secciones = computed<{ to: string; texto: string; icono: IconoNombre; cuenta: string | null; activa: boolean }[]>(() => [
  { to: '/', texto: 'Estado', icono: 'estado', cuenta: null, activa: route.path === '/' },
  {
    to: '/conversaciones',
    texto: 'Conversaciones',
    icono: 'conversaciones',
    cuenta: null,
    activa: route.path.startsWith('/conversaciones'),
  },
  {
    to: '/sesiones',
    texto: 'Sesiones',
    icono: 'sesiones',
    cuenta: abiertas.value ? `${abiertas.value} ${abiertas.value === 1 ? 'abierta' : 'abiertas'}` : null,
    activa: route.path.startsWith('/sesiones'),
  },
  {
    to: '/clientes',
    texto: 'Clientes',
    icono: 'clientes',
    cuenta: clients.loaded ? String(clients.items.length) : null,
    activa: route.path.startsWith('/clientes'),
  },
])

watch(() => route.fullPath, () => (menu.value = false))

function salir() {
  session.signOut()
  void router.replace('/conectar')
}
</script>

<template>
  <nav class="barra" aria-label="Secciones">
    <RouterLink to="/" class="marca" aria-label="Fogar Gateway, Estado">
      <FgPalabra class="palabra" />
      <span class="fg-meta">Gateway</span>
    </RouterLink>
    <div class="enlaces">
      <RouterLink v-for="s in secciones" :key="s.to" :to="s.to" :aria-current="s.activa ? 'page' : undefined">
        <FgIcono :nombre="s.icono" />{{ s.texto }}<small v-if="s.cuenta">{{ s.cuenta }}</small>
      </RouterLink>
    </div>
    <div class="pie">
      <span v-if="props.nombre" class="fg-meta">{{ props.nombre }}<template v-if="props.version"> · v{{ props.version }}</template></span>
      <button type="button" class="salir" @click="salir">Salir</button>
    </div>
  </nav>

  <header class="cabecera">
    <RouterLink to="/" aria-label="Fogar Gateway, Estado"><FgPalabra class="palabra" /></RouterLink>
    <button type="button" class="menu-boton" :aria-expanded="menu" aria-controls="menu-movil" aria-label="Menú" @click="menu = !menu">
      <FgIcono :nombre="menu ? 'cerrar' : 'menu'" />
    </button>
  </header>
  <div v-if="menu" id="menu-movil" class="menu-movil">
    <span v-if="props.nombre" class="fg-meta">{{ props.nombre }}<template v-if="props.version"> · v{{ props.version }}</template></span>
    <button type="button" class="salir" @click="salir">Salir</button>
  </div>
  <nav class="inferior" aria-label="Secciones">
    <RouterLink v-for="s in secciones" :key="s.to" :to="s.to" :aria-current="s.activa ? 'page' : undefined">
      <FgIcono :nombre="s.icono" :tam="20" />{{ s.texto }}
    </RouterLink>
  </nav>
</template>

<style scoped>
.barra {
  position: sticky;
  top: 0;
  align-self: flex-start;
  flex: 0 0 232px;
  height: 100vh;
  padding: 24px 14px;
  display: flex;
  flex-direction: column;
  gap: 28px;
  border-right: 1px solid var(--veta);
}
.marca {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 10px;
}
.marca .fg-meta {
  padding-top: 6px;
}
.palabra {
  height: 28px;
  width: auto;
  color: var(--hueso);
}
.enlaces {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.enlaces a {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 40px;
  padding: 0 10px;
  border-radius: 10px;
  color: var(--hueso-suave);
  font-weight: 550;
  transition:
    background-color var(--t-rapido) ease,
    color var(--t-rapido) ease;
}
.enlaces a:hover {
  color: var(--hueso);
  background: rgb(236 230 220 / 0.04);
}
.enlaces a[aria-current='page'] {
  color: var(--hueso);
  background: var(--piedra-alta);
}
.enlaces small {
  margin-left: auto;
  font-weight: 400;
  font-size: 13px;
  color: var(--hueso-tenue);
}
.pie {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
  padding: 0 10px;
}
.salir {
  height: 36px;
  padding: 0 14px;
  border: 0;
  border-radius: 999px;
  background: var(--velo);
  color: var(--hueso);
  font-weight: 550;
  font-size: 14px;
  cursor: pointer;
}
.salir:hover {
  background: var(--velo-alto);
}
.cabecera,
.inferior,
.menu-movil {
  display: none;
}

@media (max-width: 760px) {
  .barra {
    display: none;
  }
  .cabecera {
    position: sticky;
    top: 0;
    z-index: 10;
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    height: 56px;
    padding: 0 8px 0 16px;
    background: var(--noche);
    border-bottom: 1px solid var(--veta);
  }
  .cabecera .palabra {
    height: 24px;
  }
  .menu-boton {
    width: 44px;
    height: 44px;
    border: 0;
    border-radius: 999px;
    background: var(--velo);
    color: var(--hueso);
    display: grid;
    place-items: center;
    cursor: pointer;
  }
  .menu-movil {
    display: flex;
    width: 100%;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 16px;
    background: var(--piedra);
    border-bottom: 1px solid var(--veta);
  }
  .inferior {
    position: fixed;
    inset: auto 0 0;
    z-index: 10;
    display: flex;
    justify-content: space-around;
    padding: 6px 8px calc(6px + env(safe-area-inset-bottom));
    background: var(--noche);
    border-top: 1px solid var(--veta);
  }
  .inferior a {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    flex: 1 1 0;
    min-width: 0;
    min-height: 48px;
    justify-content: center;
    font-size: 12px;
    font-weight: 550;
    color: var(--hueso-suave);
  }
  .inferior a[aria-current='page'] {
    color: var(--hueso);
  }
}
</style>
