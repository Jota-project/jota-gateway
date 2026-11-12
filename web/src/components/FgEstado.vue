<script lang="ts">
// Estado con su icono de forma propia (fogar-brand/iconos/estados) y su texto: el color nunca va solo.
// ok → correcto (cuadrado con tic) · warn → degradado (rombo) · bad → error (triángulo) · off → no disponible.
export type EstadoTono = 'ok' | 'warn' | 'bad' | 'off'
</script>

<script setup lang="ts">
defineProps<{ tono: EstadoTono; texto?: string; tam?: number }>()
</script>

<template>
  <span class="estado">
    <svg :width="tam ?? 14" :height="tam ?? 14" viewBox="0 0 14 14" aria-hidden="true" focusable="false">
      <template v-if="tono === 'ok'">
        <rect x="1" y="1" width="12" height="12" rx="3.5" fill="#78b8f0" />
        <path d="M4 7.3l2 2 4.1-4.5" stroke="#0b1012" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" />
      </template>
      <template v-else-if="tono === 'warn'">
        <path d="M7 1.2L12.8 7 7 12.8 1.2 7z" fill="none" stroke="#e6df7a" stroke-width="1.6" stroke-linejoin="round" />
        <path d="M7 1.2L12.8 7 7 12.8z" fill="#e6df7a" />
      </template>
      <template v-else-if="tono === 'bad'">
        <path d="M7 1.4L13.1 12.3H.9z" fill="#ff8db4" stroke="#ff8db4" stroke-width=".8" stroke-linejoin="round" />
        <path d="M7 5.1v3.4" stroke="#0b1012" stroke-width="1.7" stroke-linecap="round" />
        <circle cx="7" cy="10.3" r=".95" fill="#0b1012" />
      </template>
      <rect v-else x="1.8" y="1.8" width="10.4" height="10.4" rx="3" fill="none" stroke="#8f99a1" stroke-width="1.6" stroke-dasharray="2.6 2" />
    </svg>
    <span v-if="texto">{{ texto }}</span><slot />
  </span>
</template>

<style scoped>
.estado {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
svg {
  flex: none;
}
</style>
