<script setup lang="ts">
// Hueco de carga con brillo que recorre. Para listas y tarjetas, no para el contenido de una respuesta.
const props = withDefaults(defineProps<{ width?: string; height?: string; radius?: string }>(), {
  width: '100%',
  height: '14px',
  radius: '10px',
})
</script>

<template>
  <span class="skeleton" aria-hidden="true" :style="{ width: props.width, height: props.height, borderRadius: props.radius }" />
</template>

<style scoped>
.skeleton {
  position: relative;
  display: block;
  overflow: hidden;
  background: rgb(246 243 240 / 0.06);
}
.skeleton::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgb(246 243 240 / 0.07), transparent);
  transform: translateX(-100%);
  animation: j-shimmer 1.5s var(--ease-in-out) infinite;
}
</style>
