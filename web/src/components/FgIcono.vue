<script setup lang="ts">
// Iconos de interfaz: SVG lineales de trazo 1,75 redondeado en currentColor (fogar-brand: «propuesto»).
// Decorativos: el texto de al lado o el aria-label del botón dice qué son.
export type IconoNombre = 'estado' | 'sesiones' | 'clientes' | 'menu' | 'mas' | 'buscar' | 'izquierda' | 'derecha' | 'cerrar' | 'copiar'

defineProps<{ nombre: IconoNombre; tam?: number }>()
</script>

<template>
  <svg
    :width="tam ?? 18"
    :height="tam ?? 18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <template v-if="nombre === 'estado'">
      <circle cx="5" cy="12" r="2.5" /><circle cx="19" cy="6" r="2.5" /><circle cx="19" cy="18" r="2.5" />
      <path d="M7.5 12h4l5-5M11.5 12l5 5" />
    </template>
    <path v-else-if="nombre === 'sesiones'" d="M4 6h16M4 12h16M4 18h10" />
    <template v-else-if="nombre === 'clientes'"><rect x="4" y="3" width="16" height="12" rx="2" /><path d="M8 21h8M12 15v6" /></template>
    <path v-else-if="nombre === 'menu'" d="M4 7h16M4 12h16M4 17h16" />
    <path v-else-if="nombre === 'mas'" d="M12 5v14M5 12h14" />
    <template v-else-if="nombre === 'buscar'"><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.2-4.2" /></template>
    <path v-else-if="nombre === 'izquierda'" d="M15 6l-6 6 6 6" />
    <path v-else-if="nombre === 'derecha'" d="M9 6l6 6-6 6" />
    <path v-else-if="nombre === 'cerrar'" d="M6 6l12 12M18 6L6 18" />
    <template v-else-if="nombre === 'copiar'"><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" /></template>
  </svg>
</template>
