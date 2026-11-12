<script setup lang="ts">
// Abre y cierra su contenido animando la altura con grid (sin medir en JavaScript).
// Para mensajes de error, detalles de una fila o una línea de aviso.
const props = defineProps<{ open: boolean }>()
</script>

<template>
  <div class="reveal" :class="{ open: props.open }" :inert="!props.open || undefined">
    <div class="inner"><slot /></div>
  </div>
</template>

<style scoped>
.reveal {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  transition:
    grid-template-rows var(--t-scene) var(--ease),
    opacity var(--t-base) ease;
}
.reveal.open {
  grid-template-rows: 1fr;
  opacity: 1;
}
.inner {
  min-height: 0;
  overflow: hidden;
}
</style>
