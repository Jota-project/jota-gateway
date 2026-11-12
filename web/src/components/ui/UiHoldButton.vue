<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import AppIcon, { type IconName } from '@/components/AppIcon.vue'
import { reducedMotion } from '@/lib/motion'

// Mantener para confirmar: sustituye a la ventana de «¿Seguro?» en lo que se puede deshacer mal
// (rotar una clave, borrar un cliente). El relleno avanza lineal mientras mantienes; soltar lo devuelve rápido.
// Con teclado: mantener la barra espaciadora o Intro.
const props = withDefaults(
  defineProps<{ icon?: IconName; doneLabel?: string; duration?: number; tone?: 'bad' | 'accent' }>(),
  { icon: 'clave', doneLabel: 'Hecho', duration: 1400, tone: 'bad' },
)
const emit = defineEmits<{ confirm: [] }>()

const holding = ref(false)
const done = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let reset: ReturnType<typeof setTimeout> | undefined

function start(): void {
  if (done.value || holding.value) return
  holding.value = true
  timer = setTimeout(
    () => {
      holding.value = false
      done.value = true
      emit('confirm')
      reset = setTimeout(() => (done.value = false), 2600)
    },
    reducedMotion.value ? 300 : props.duration,
  )
}

function stop(): void {
  clearTimeout(timer)
  holding.value = false
}

function onPointerDown(event: PointerEvent): void {
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  start()
}

function onKey(event: KeyboardEvent, down: boolean): void {
  if (event.key !== ' ' && event.key !== 'Enter') return
  event.preventDefault()
  if (down && !event.repeat) start()
  if (!down) stop()
}

onBeforeUnmount(() => {
  clearTimeout(timer)
  clearTimeout(reset)
})
</script>

<template>
  <button
    type="button"
    class="hold"
    :class="[props.tone, { holding, done }]"
    :style="{ '--hold': `${props.duration}ms` }"
    @pointerdown="onPointerDown"
    @pointerup="stop"
    @pointercancel="stop"
    @keydown="onKey($event, true)"
    @keyup="onKey($event, false)"
    @click.prevent
  >
    <span class="fill" aria-hidden="true" />
    <Transition name="j-swap">
      <span v-if="done" key="done" class="content"><AppIcon name="check" />{{ props.doneLabel }}</span>
      <span v-else key="idle" class="content"><AppIcon :name="props.icon" /><slot /></span>
    </Transition>
    <span v-if="!done" class="content over" aria-hidden="true"><AppIcon :name="props.icon" /><slot /></span>
  </button>
</template>

<style scoped>
.hold {
  --tint: var(--bad);
  --on-tint: #2a1714;
  position: relative;
  isolation: isolate;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 44px;
  padding: 0 20px;
  border: 0;
  border-radius: 999px;
  overflow: hidden;
  background: rgb(239 138 124 / 0.1);
  color: var(--tint);
  box-shadow: inset 0 0 0 1px rgb(239 138 124 / 0.32);
  font-size: 15px;
  font-weight: 550;
  white-space: nowrap;
  cursor: pointer;
  user-select: none;
  touch-action: none;
  -webkit-tap-highlight-color: transparent;
  transition:
    transform var(--t-press) var(--ease-out),
    background-color var(--t-quick) ease,
    color var(--t-quick) ease;
}
.hold.accent {
  --tint: var(--accent-text);
  --on-tint: #1d1825;
  background: rgb(124 106 174 / 0.12);
  box-shadow: inset 0 0 0 1px rgb(210 201 238 / 0.3);
}
.holding {
  transform: scale(0.97);
}
.content {
  display: inline-flex;
  align-items: center;
  gap: 9px;
}
.fill,
.over {
  position: absolute;
  inset: 0;
  clip-path: inset(0 100% 0 0);
  transition: clip-path 220ms var(--ease-out); /* soltar: rápido */
}
.fill {
  z-index: -1;
  background: var(--tint);
}
.over {
  justify-content: center;
  color: var(--on-tint);
}
.holding .fill,
.holding .over {
  clip-path: inset(0 0 0 0);
  transition: clip-path var(--hold) linear; /* mantener: lento y a ritmo constante, es una barra de progreso */
}
.done {
  color: var(--status);
  background: rgb(143 168 216 / 0.12);
  box-shadow: inset 0 0 0 1px rgb(143 168 216 / 0.3);
}
</style>
