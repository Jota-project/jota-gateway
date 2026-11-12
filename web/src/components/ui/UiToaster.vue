<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from '@/components/AppIcon.vue'
import { reducedMotion } from '@/lib/motion'
import { dismissToast, toasts, type Toast } from '@/lib/toasts'

// Avisos abajo a la derecha. Suben desde abajo y se apilan encogiéndose; al pasar el ratón (o el foco)
// se despliegan. Se van solos a los 4,2 s, pero no mientras están desplegados ni con la pestaña oculta.
// Arrastrar a la derecha los quita: basta un gesto rápido (Sonner). Se monta una vez, en App.vue.
const SHOW_MS = 4200
const GAP = 10

const expanded = ref(false)
const height = ref(0)
const items = new Map<number, HTMLElement>()
const timers = new Map<number, ReturnType<typeof setTimeout>>()
const dragging = new Set<number>()

function setRef(id: number, el: unknown): void {
  if (el instanceof HTMLElement) items.set(id, el)
  else items.delete(id)
}

function layout(): void {
  let y = 0
  toasts.forEach((toast, i) => {
    const el = items.get(toast.id)
    if (!el || dragging.has(toast.id)) return
    el.style.transform = expanded.value ? `translateY(${-y}px)` : `translateY(${-i * 12}px) scale(${1 - i * 0.05})`
    el.style.opacity = !expanded.value && i > 2 ? '0' : '1'
    el.style.zIndex = String(100 - i)
    y += el.offsetHeight + GAP
  })
  const first = toasts[0] ? items.get(toasts[0].id) : undefined
  height.value = expanded.value ? y : (first?.offsetHeight ?? 0)
}

function arm(id: number): void {
  clearTimeout(timers.get(id))
  timers.set(
    id,
    setTimeout(() => (expanded.value || document.hidden ? arm(id) : close(id)), SHOW_MS),
  )
}

function close(id: number): void {
  clearTimeout(timers.get(id))
  timers.delete(id)
  dismissToast(id)
}

function setExpanded(value: boolean): void {
  expanded.value = value
  layout()
}

// Entrar y salir los lleva la lista con estas dos funciones: el transform lo gobierna layout().
function onEnter(el: Element, done: () => void): void {
  const node = el as HTMLElement
  node.style.transform = 'translateY(110%)'
  node.style.opacity = '0'
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      layout()
      setTimeout(done, 400)
    }),
  )
}
function onLeave(el: Element, done: () => void): void {
  const node = el as HTMLElement
  node.style.transitionDuration = '200ms'
  node.style.transitionTimingFunction = 'var(--ease-out)'
  node.style.opacity = '0'
  setTimeout(done, reducedMotion.value ? 0 : 210)
}

watch(
  () => toasts.map((t) => t.id),
  (ids, old = []) => {
    ids.filter((id) => !old.includes(id)).forEach(arm)
    void nextTick(layout)
  },
)

// Arrastrar a un lado para quitarlo.
let startX = 0
let startT = 0
let dx = 0
function onDown(event: PointerEvent, toast: Toast): void {
  if ((event.target as HTMLElement).closest('button')) return
  const el = items.get(toast.id)
  if (!el) return
  dragging.add(toast.id)
  startX = event.clientX
  startT = performance.now()
  dx = 0
  el.setPointerCapture(event.pointerId)
  el.style.transition = 'none'
}
function onMove(event: PointerEvent, toast: Toast): void {
  const el = items.get(toast.id)
  if (!el || !dragging.has(toast.id)) return
  dx = event.clientX - startX
  const shown = dx < 0 ? dx / (1 + Math.abs(dx) / 80) : dx // hacia la izquierda cuesta cada vez más
  el.style.transform = `translateX(${shown}px)`
  el.style.opacity = String(1 - Math.min(Math.abs(dx) / 260, 0.6))
}
function onUp(toast: Toast): void {
  const el = items.get(toast.id)
  if (!el || !dragging.has(toast.id)) return
  dragging.delete(toast.id)
  el.style.transition = ''
  const velocity = Math.abs(dx) / (performance.now() - startT)
  if (dx > 90 || (dx > 20 && velocity > 0.11)) {
    el.style.transform = 'translateX(120%)'
    close(toast.id)
  } else layout()
}

onMounted(() => toasts.forEach((t) => arm(t.id)))
onBeforeUnmount(() => timers.forEach((t) => clearTimeout(t)))
</script>

<template>
  <section
    class="toaster"
    aria-label="Avisos"
    :style="{ height: `${height}px` }"
    @pointerenter="setExpanded(true)"
    @pointerleave="setExpanded(false)"
    @focusin="setExpanded(true)"
    @focusout="setExpanded(false)"
  >
    <TransitionGroup :css="false" @enter="onEnter" @leave="onLeave">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        :ref="(el) => setRef(toast.id, el)"
        class="toast"
        :class="toast.tone"
        :role="toast.tone === 'bad' ? 'alert' : 'status'"
        @pointerdown="onDown($event, toast)"
        @pointermove="onMove($event, toast)"
        @pointerup="onUp(toast)"
        @pointercancel="onUp(toast)"
      >
        <AppIcon :name="toast.tone === 'ok' ? 'check' : 'aviso'" class="icon" />
        <div class="text">
          <b>{{ toast.title }}</b>
          <span v-if="toast.body">{{ toast.body }}</span>
        </div>
        <button type="button" class="close" aria-label="Cerrar aviso" @click="close(toast.id)">
          <AppIcon name="cerrar" :size="16" />
        </button>
      </div>
    </TransitionGroup>
  </section>
</template>

<style scoped>
.toaster {
  position: fixed;
  z-index: 50;
  right: max(24px, calc(env(safe-area-inset-right, 0px) + 16px));
  bottom: calc(env(safe-area-inset-bottom, 0px) + 24px);
  width: min(360px, calc(100vw - 32px));
}
.toast {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 100%;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 14px 14px 16px;
  border-radius: 18px;
  background: rgb(45 38 55 / 0.94);
  -webkit-backdrop-filter: blur(16px) saturate(150%);
  backdrop-filter: blur(16px) saturate(150%);
  box-shadow:
    0 0 0 1px rgb(246 243 240 / 0.07),
    0 18px 40px -12px var(--shadow);
  transform-origin: bottom center;
  touch-action: pan-y;
  user-select: none;
  transition:
    transform 400ms ease,
    opacity 400ms ease;
}
.icon {
  margin-top: 1px;
  flex: none;
  color: var(--status);
}
.warn .icon {
  color: var(--warn);
}
.bad .icon {
  color: var(--bad);
}
.text {
  min-width: 0;
}
.text b {
  display: block;
  font-weight: 550;
}
.text span {
  display: block;
  font-size: 13.5px;
  color: var(--text-soft);
}
.close {
  flex: none;
  width: 28px;
  height: 28px;
  margin-left: auto;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 50%;
  background: none;
  color: var(--text-faint);
  cursor: pointer;
  transition:
    background-color var(--t-quick) ease,
    color var(--t-quick) ease;
}
@media (hover: hover) and (pointer: fine) {
  .close:hover {
    background: rgb(246 243 240 / 0.08);
    color: var(--text);
  }
}
@media (prefers-reduced-transparency: reduce) {
  .toast {
    background: var(--surface);
    -webkit-backdrop-filter: none;
    backdrop-filter: none;
  }
}
</style>
