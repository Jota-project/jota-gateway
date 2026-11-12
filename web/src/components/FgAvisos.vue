<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'
import { dismissToast, toasts, type ToastTone } from '@/lib/toasts'
import FgEstado, { type EstadoTono } from './FgEstado.vue'
import FgIcono from './FgIcono.vue'

// Avisos breves (copiado, guardado, error al guardar). Se van solos a los 5 s; los de error se quedan
// hasta cerrarlos. Se anuncian por una región viva educada.
const TONO: Record<ToastTone, EstadoTono> = { ok: 'ok', warn: 'warn', bad: 'bad' }
const timers = new Map<number, ReturnType<typeof setTimeout>>()

watch(
  () => toasts.map((t) => t.id),
  () => {
    for (const t of toasts) {
      if (t.tone !== 'bad' && !timers.has(t.id)) timers.set(t.id, setTimeout(() => dismissToast(t.id), 5000))
    }
  },
  { immediate: true },
)
onBeforeUnmount(() => timers.forEach(clearTimeout))
</script>

<template>
  <div class="avisos" role="status" aria-live="polite">
    <TransitionGroup name="aviso">
      <div v-for="t in toasts" :key="t.id" class="aviso">
        <FgEstado :tono="TONO[t.tone]" />
        <div class="texto">
          <p class="titulo">{{ t.title }}</p>
          <p v-if="t.body" class="cuerpo">{{ t.body }}</p>
        </div>
        <button type="button" class="cerrar" aria-label="Cerrar aviso" @click="dismissToast(t.id)">
          <FgIcono nombre="cerrar" :tam="16" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.avisos {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(380px, calc(100vw - 32px));
  pointer-events: none;
}
.aviso {
  pointer-events: auto;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: start;
  gap: 10px;
  padding: 12px 8px 12px 14px;
  border-radius: var(--radio-nodo);
  background: var(--piedra-alta);
  box-shadow: inset 0 0 0 1px var(--veta), 0 18px 40px -20px rgb(0 0 0 / 0.7);
}
.aviso :deep(.estado) {
  padding-top: 3px;
}
.titulo {
  margin: 0;
  font-weight: 550;
}
.cuerpo {
  margin: 2px 0 0;
  font-size: 14px;
  color: var(--hueso-suave);
}
.cerrar {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 999px;
  background: none;
  color: var(--hueso-suave);
  display: grid;
  place-items: center;
  cursor: pointer;
}
.cerrar:hover {
  background: var(--velo);
  color: var(--hueso);
}
.aviso-enter-active,
.aviso-leave-active {
  transition:
    opacity var(--t-base) var(--ease-out),
    transform var(--t-base) var(--ease-out);
}
.aviso-enter-from,
.aviso-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
</style>
