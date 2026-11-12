<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useLiveStore } from '@/stores/live'
import { useSessionStore } from '@/stores/session'
import AppIcon, { type IconName } from './AppIcon.vue'
import ServiceHealth from './ServiceHealth.vue'

const LINKS: Array<{ to: string; label: string; icon: IconName }> = [
  { to: '/', label: 'Chat', icon: 'chat' },
  { to: '/estado', label: 'Estado', icon: 'estado' },
  { to: '/sesiones', label: 'Sesiones', icon: 'sesiones' },
  { to: '/clientes', label: 'Clientes', icon: 'clientes' },
]

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const live = useLiveStore()

// La píldora de la sección activa viaja con muelle de un enlace a otro en vez de aparecer en el nuevo.
const links = useTemplateRef<HTMLElement>('links')
const pill = ref({ x: 0, y: 0, w: 0, h: 0, on: false })
const ready = ref(false)

function place(): void {
  const el = links.value?.querySelector<HTMLElement>('.link.active')
  pill.value = el
    ? { x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight, on: true }
    : { ...pill.value, on: false }
}

watch(
  () => route.path,
  () => void nextTick(place),
)
let observer: ResizeObserver | undefined
onMounted(() => {
  place()
  requestAnimationFrame(() => (ready.value = true))
  observer = new ResizeObserver(place)
  if (links.value) observer.observe(links.value)
})
onBeforeUnmount(() => observer?.disconnect())

function signOut(): void {
  live.halt()
  session.signOut()
  void router.replace('/conectar')
}
</script>

<template>
  <nav class="rail" aria-label="Secciones">
    <div class="brand">
      <span class="mark" aria-hidden="true">J</span>
      <span class="name">Fogar</span>
    </div>

    <div ref="links" class="links">
      <span
        class="pill"
        :class="{ ready, on: pill.on }"
        :style="{ width: `${pill.w}px`, height: `${pill.h}px`, transform: `translate(${pill.x}px, ${pill.y}px)` }"
        aria-hidden="true"
      />
      <RouterLink v-for="link in LINKS" :key="link.to" :to="link.to" class="link" active-class="" exact-active-class="active">
        <AppIcon :name="link.icon" />
        {{ link.label }}
      </RouterLink>
    </div>

    <div class="foot">
      <ServiceHealth />
      <button type="button" class="link out" @click="signOut">
        <AppIcon name="salir" :size="18" />
        Salir
      </button>
    </div>
  </nav>
</template>

<style scoped>
.rail {
  position: relative;
  z-index: 1;
  flex: 0 0 248px;
  display: flex;
  flex-direction: column;
  gap: 28px;
  padding: 28px 20px 24px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 6px;
}
.mark {
  width: 36px;
  height: 36px;
  border-radius: 11px;
  background: linear-gradient(135deg, var(--malva), var(--cielo));
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--serif);
  font-size: 22px;
  font-weight: 500;
  color: #fff;
}
.name {
  font-family: var(--serif);
  font-size: 22px;
  font-variation-settings: 'opsz' 144;
  letter-spacing: -0.01em;
}
.links {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.pill {
  position: absolute;
  top: 0;
  left: 0;
  border-radius: 14px;
  background: rgb(246 243 240 / 0.07);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--t-quick) ease;
}
.pill.on {
  opacity: 1;
}
.pill.ready {
  transition:
    transform var(--t-scene) var(--spring),
    width var(--t-scene) var(--spring),
    height var(--t-scene) var(--spring),
    opacity var(--t-quick) ease;
}
.link {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 12px;
  height: 44px;
  padding: 0 14px;
  border: 0;
  border-radius: 14px;
  background: transparent;
  color: var(--text-soft);
  font-size: 15px;
  font-weight: 500;
  text-decoration: none;
  cursor: pointer;
  transition:
    background-color var(--t-quick) ease,
    color var(--t-quick) ease,
    transform var(--t-press) var(--ease-out);
}
.link:active {
  transform: scale(0.98);
}
.link svg {
  transition: color var(--t-quick) ease;
}
@media (hover: hover) and (pointer: fine) {
  .link:not(.active):hover {
    background: rgb(246 243 240 / 0.04);
    color: var(--text);
  }
}
.link.active {
  color: var(--text);
}
.link.active svg {
  color: var(--accent-text);
}
.foot {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.out {
  height: 40px;
  font-size: 14px;
}

@media (max-width: 760px) {
  .rail {
    flex: 1 1 100%;
    padding: 16px;
    gap: 16px;
  }
  .links {
    flex-direction: row;
    flex-wrap: wrap;
  }
  .foot {
    display: none;
  }
}
</style>
