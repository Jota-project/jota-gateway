<script setup lang="ts">
// Hueco honesto para las pantallas que aún no están hechas en el esqueleto.
defineProps<{ title: string; block: string; summary: string }>()
</script>

<template>
  <div class="pending">
    <h1 class="display">{{ title }}</h1>
    <p>{{ summary }}</p>
    <p class="block">Llega en el bloque {{ block }} de M17.</p>
  </div>
</template>

<style scoped>
.pending {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 640px;
}

h1 {
  margin: 0 0 6px;
  font-size: 44px;
  line-height: 1.05;
}

p {
  margin: 0;
  color: var(--text-soft);
}

.block {
  font-size: 14px;
  color: var(--text-faint);
}
</style>
