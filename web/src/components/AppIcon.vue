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
  check: [{ tag: 'path', d: 'M5 12.5l4.5 4.5L19 7.5' }],
  chevron: [{ tag: 'path', d: 'M9 6l6 6-6 6' }],
  cerrar: [{ tag: 'path', d: 'M6 6l12 12M18 6L6 18' }],
  mas: [{ tag: 'path', d: 'M12 5v14M5 12h14' }],
  copiar: [
    { tag: 'rect', x: 8, y: 8, width: 12, height: 12, rx: 2.5 },
    { tag: 'path', d: 'M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2' },
  ],
  clave: [
    { tag: 'circle', cx: 8, cy: 15, r: 4 },
    { tag: 'path', d: 'M11 12l8-8M16 7l2.5 2.5M14 9l2 2' },
  ],
  ver: [
    { tag: 'path', d: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z' },
    { tag: 'circle', cx: 12, cy: 12, r: 3 },
  ],
  aviso: [
    { tag: 'path', d: 'M12 4l9 16H3z' },
    { tag: 'path', d: 'M12 10v4M12 17v.5' },
  ],
  micro: [
    { tag: 'rect', x: 9, y: 3.5, width: 6, height: 11, rx: 3 },
    { tag: 'path', d: 'M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5v3' },
  ],
  enviar: [{ tag: 'path', d: 'M12 19V5M6 11l6-6 6 6' }],
  parar: [{ tag: 'rect', x: 7, y: 7, width: 10, height: 10, rx: 2.5 }],
  voz: [{ tag: 'path', d: 'M3 12c2.5-4 4.5-4 7 0s4.5 4 7 0 3-2 4-1' }],
  emparejar: [
    { tag: 'path', d: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1' },
    { tag: 'path', d: 'M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1' },
  ],
  herramienta: [{ tag: 'path', d: 'M14.5 6.5a4 4 0 0 0 5 5L12 19a2.1 2.1 0 0 1-3-3l7.5-7.5a4 4 0 0 0-2-2z' }],
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
