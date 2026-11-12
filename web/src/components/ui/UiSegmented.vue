<script setup lang="ts" generic="T extends string">
import { nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue'

// Selector de una sola opción. La píldora viaja con muelle hasta la elegida; flechas del teclado para moverse.
const props = withDefaults(defineProps<{ options: { value: T; label: string }[]; label: string; size?: 'md' | 'sm' }>(), {
  size: 'md',
})
const model = defineModel<T>({ required: true })

const root = useTemplateRef<HTMLElement>('root')
const pill = ref({ x: 0, width: 0 })
const ready = ref(false) // sin transición en la primera colocación

function place(): void {
  const el = root.value?.querySelector<HTMLElement>('[aria-checked="true"]')
  if (el) pill.value = { x: el.offsetLeft, width: el.offsetWidth }
}

function pick(value: T, focus = false): void {
  model.value = value
  if (focus) void nextTick(() => root.value?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus())
}

function onKey(event: KeyboardEvent, index: number): void {
  const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
  if (!step) return
  event.preventDefault()
  const next = props.options[(index + step + props.options.length) % props.options.length]
  if (next) pick(next.value, true)
}

watch(model, () => void nextTick(place))

let observer: ResizeObserver | undefined
onMounted(() => {
  place()
  requestAnimationFrame(() => (ready.value = true))
  observer = new ResizeObserver(place)
  if (root.value) observer.observe(root.value)
  void document.fonts?.ready.then(place)
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div ref="root" class="seg" :class="props.size" role="radiogroup" :aria-label="props.label">
    <span
      class="pill"
      :class="{ ready }"
      :style="{ width: `${pill.width}px`, transform: `translateX(${pill.x}px)` }"
      aria-hidden="true"
    />
    <button
      v-for="(option, i) in props.options"
      :key="option.value"
      type="button"
      role="radio"
      :aria-checked="model === option.value"
      :tabindex="model === option.value ? 0 : -1"
      @click="pick(option.value)"
      @keydown="onKey($event, i)"
    >
      {{ option.label }}
    </button>
  </div>
</template>

<style scoped>
.seg {
  position: relative;
  display: inline-grid;
  grid-auto-flow: column;
  grid-auto-columns: 1fr;
  padding: 4px;
  border-radius: 999px;
  background: rgb(246 243 240 / 0.06);
}
button {
  position: relative;
  z-index: 1;
  height: 36px;
  padding: 0 16px;
  border: 0;
  border-radius: 999px;
  background: none;
  color: var(--text-soft);
  font-size: 14px;
  font-weight: 550;
  white-space: nowrap;
  cursor: pointer;
  transition: color var(--t-quick) ease;
}
.sm button {
  height: 30px;
  padding: 0 12px;
  font-size: 13px;
}
button[aria-checked='true'] {
  color: var(--text);
}
.pill {
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 0;
  border-radius: 999px;
  background: var(--surface-3);
  box-shadow: 0 4px 12px rgb(10 8 14 / 0.35);
}
.pill.ready {
  transition:
    transform var(--t-scene) var(--spring),
    width var(--t-scene) var(--spring);
}
</style>
