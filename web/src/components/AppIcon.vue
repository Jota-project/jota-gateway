<script lang="ts">
// Iconos lineales de trazo redondeado (Jota Dusk aún no tiene set propio): SVG en línea, sin emojis.
type Shape =
  | { tag: 'path'; d: string }
  | { tag: 'circle'; cx: number; cy: number; r: number }
  | { tag: 'rect'; x: number; y: number; width: number; height: number; rx: number }

const ICONS = {
  chat: [{ tag: 'path', d: 'M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H9l-5 4z' }],
  estado: [{ tag: 'path', d: 'M3 12h4l3-7 4 14 3-7h4' }],
  sesiones: [
    { tag: 'circle', cx: 12, cy: 12, r: 8 },
    { tag: 'path', d: 'M12 8v4l2.5 2' },
  ],
  clientes: [
    { tag: 'rect', x: 3, y: 5, width: 12, height: 9, rx: 2 },
    { tag: 'rect', x: 17, y: 8, width: 4, height: 11, rx: 1.5 },
    { tag: 'path', d: 'M7 18h6' },
  ],
  salir: [
    { tag: 'path', d: 'M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4' },
    { tag: 'path', d: 'M10 16l-4-4 4-4' },
    { tag: 'path', d: 'M6 12h10' },
  ],
  reintentar: [
    { tag: 'path', d: 'M20 12a8 8 0 1 1-2.3-5.6' },
    { tag: 'path', d: 'M20 4v5h-5' },
  ],
} satisfies Record<string, Shape[]>

export type IconName = keyof typeof ICONS
</script>

<script setup lang="ts">
const props = withDefaults(defineProps<{ name: IconName; size?: number }>(), { size: 20 })
</script>

<template>
  <svg
    :width="props.size"
    :height="props.size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <template v-for="(shape, i) in ICONS[props.name]" :key="i">
      <path v-if="shape.tag === 'path'" :d="shape.d" />
      <circle v-else-if="shape.tag === 'circle'" :cx="shape.cx" :cy="shape.cy" :r="shape.r" />
      <rect v-else :x="shape.x" :y="shape.y" :width="shape.width" :height="shape.height" :rx="shape.rx" />
    </template>
  </svg>
</template>
