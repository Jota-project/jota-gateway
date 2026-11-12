<script setup lang="ts">
import FgIcono, { type IconoNombre } from './FgIcono.vue'

// El botón de Fogar: píldora. `principal` es la tinta invertida (una por pantalla); `discreto`, un velo
// de hueso. Se hunde al 97 % en 120 ms; desactivado, opacidad 0,42.
withDefaults(
  defineProps<{
    variante?: 'principal' | 'discreto'
    tam?: 'md' | 'sm'
    icono?: IconoNombre
    /** Sin texto visible: el botón necesita `aria-label`. */
    soloIcono?: boolean
    ocupado?: boolean
    type?: 'button' | 'submit'
  }>(),
  { variante: 'discreto', tam: 'md', type: 'button', icono: undefined },
)
</script>

<template>
  <button :type="type" class="boton" :class="[variante, tam, { 'solo-icono': soloIcono }]" :aria-busy="ocupado || undefined">
    <span v-if="ocupado" class="giro" aria-hidden="true" />
    <FgIcono v-else-if="icono" :nombre="icono" :tam="tam === 'sm' ? 16 : 18" />
    <slot />
  </button>
</template>

<style scoped>
.boton {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 44px;
  padding: 0 20px;
  border: 0;
  border-radius: var(--radio-pildora);
  background: var(--velo);
  color: var(--hueso);
  font-size: 15px;
  font-weight: 550;
  white-space: nowrap;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition:
    transform var(--t-pulsar) var(--ease-out),
    background-color var(--t-rapido) ease,
    opacity var(--t-rapido) ease;
}
.boton:active:not(:disabled) {
  transform: scale(0.97);
}
.boton:disabled {
  opacity: 0.42;
  cursor: default;
}
.principal {
  background: var(--accion);
  color: var(--sobre-accion);
}
@media (hover: hover) {
  .discreto:hover:not(:disabled) {
    background: var(--velo-alto);
  }
  .principal:hover:not(:disabled) {
    background: #fff8ee;
  }
}
.sm {
  height: 36px;
  padding: 0 14px;
  font-size: 14px;
}
.solo-icono {
  width: 44px;
  padding: 0;
}
.sm.solo-icono {
  width: 36px;
}
.giro {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid currentColor;
  border-right-color: transparent;
  animation: giro 0.8s linear infinite;
}
@keyframes giro {
  to {
    transform: rotate(1turn);
  }
}
</style>
