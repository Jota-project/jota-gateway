<script setup lang="ts">
import { useTemplateRef, watch } from 'vue'
import AppIcon from '@/components/AppIcon.vue'
import { reducedMotion } from '@/lib/motion'

// Orbe de voz: la cara viva de Jota. En reposo late (3,4 s, escala 1 → 1,05). Mientras se mantiene pulsado,
// escucha: crece y sus anillos siguen `level` (0–1, el nivel del micro que mide quien lo usa).
// Ratón, dedo o teclado (mantener espacio o Intro). Emite `start` al empezar y `stop` al soltar.
const props = withDefaults(defineProps<{ listening?: boolean; level?: number; label?: string; disabled?: boolean }>(), {
  listening: false,
  level: 0,
  label: 'Mantén para hablar',
  disabled: false,
})
const emit = defineEmits<{ start: []; stop: [] }>()

const core = useTemplateRef<HTMLElement>('core')
const ringA = useTemplateRef<HTMLElement>('ringA')
const ringB = useTemplateRef<HTMLElement>('ringB')

// El nivel cambia en cada fotograma: se escribe el transform directamente en cada elemento,
// sin pasar por el render de Vue ni por una variable heredada.
watch(
  () => [props.level, props.listening] as const,
  ([level, listening]) => {
    if (!core.value || !ringA.value || !ringB.value) return
    if (!listening || reducedMotion.value) {
      core.value.style.transform = ''
      for (const ring of [ringA.value, ringB.value]) {
        ring.style.opacity = '0'
        ring.style.transform = ''
      }
      return
    }
    const l = Math.min(Math.max(level, 0), 1)
    core.value.style.transform = `scale(${1.06 + l * 0.14})`
    ringA.value.style.opacity = String(0.1 + 0.55 * l)
    ringA.value.style.transform = `scale(${1.15 + l * 0.45})`
    ringB.value.style.opacity = String(0.1 + 0.35 * l)
    ringB.value.style.transform = `scale(${1.15 + l * 0.8})`
  },
)

let active = false
function begin(event?: PointerEvent): void {
  if (active || props.disabled) return
  active = true
  if (event) (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  emit('start')
}
function end(): void {
  if (!active) return
  active = false
  emit('stop')
}
function onKey(event: KeyboardEvent, down: boolean): void {
  if (event.key !== ' ' && event.key !== 'Enter') return
  event.preventDefault()
  if (down && !event.repeat) begin()
  if (!down) end()
}
</script>

<template>
  <button
    type="button"
    class="orb"
    :class="{ listening: props.listening }"
    :aria-label="props.label"
    :aria-pressed="props.listening"
    :disabled="props.disabled"
    @pointerdown="begin"
    @pointerup="end"
    @pointercancel="end"
    @keydown="onKey($event, true)"
    @keyup="onKey($event, false)"
    @contextmenu.prevent
  >
    <span ref="ringA" class="ring" />
    <span ref="ringB" class="ring" />
    <span ref="core" class="core j-orb" />
    <AppIcon name="micro" class="mic" />
  </button>
</template>

<style scoped>
.orb {
  position: relative;
  flex: none;
  width: 56px;
  height: 56px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: none;
  cursor: pointer;
  touch-action: none;
  -webkit-tap-highlight-color: transparent;
}
.orb:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.core {
  position: absolute;
  inset: 6px;
  transition: transform 360ms var(--spring);
}
.listening .core {
  animation: none;
}
.ring {
  position: absolute;
  inset: 6px;
  border-radius: 50%;
  border: 1.5px solid rgb(210 201 238 / 0.55);
  opacity: 0;
  pointer-events: none;
}
.mic {
  position: absolute;
  inset: 0;
  margin: auto;
  color: #fff;
  stroke-width: 2;
  transition: opacity var(--t-quick) ease;
}
.listening .mic {
  opacity: 0;
}
</style>
