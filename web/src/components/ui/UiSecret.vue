<script setup lang="ts">
import { ref, useTemplateRef } from 'vue'
import { wait } from '@/lib/motion'
import UiButton from './UiButton.vue'

// Un secreto que se enseña una sola vez (la clave de un cliente al crearlo a mano o al rotarla).
// Va velado hasta pulsar «Enseñar»; el velo se retira de izquierda a derecha. «Copiar» confirma en su sitio.
const props = defineProps<{ value: string; label: string }>()

const shown = ref(false)
const copied = ref(false)
const code = useTemplateRef<HTMLElement>('code')

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.value)
  } catch {
    // Sin permiso de portapapeles: se enseña y se selecciona para que se pueda copiar a mano.
    shown.value = true
    const range = document.createRange()
    if (code.value) range.selectNodeContents(code.value)
    getSelection()?.removeAllRanges()
    getSelection()?.addRange(range)
    return
  }
  copied.value = true
  await wait(1600)
  copied.value = false
}
</script>

<template>
  <div class="secret">
    <div class="box" :class="{ shown }">
      <code ref="code" class="value tab" :aria-label="props.label" :aria-hidden="!shown">{{ props.value }}</code>
      <span class="veil" aria-hidden="true" />
    </div>
    <div class="actions">
      <UiButton size="sm" icon="ver" :state-key="String(shown)" :aria-pressed="shown" @click="shown = !shown">
        {{ shown ? 'Ocultar' : 'Enseñar la clave' }}
      </UiButton>
      <UiButton size="sm" :icon="copied ? 'check' : 'copiar'" :state-key="String(copied)" @click="copy">
        {{ copied ? 'Copiada' : 'Copiar' }}
      </UiButton>
    </div>
  </div>
</template>

<style scoped>
.secret {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 440px;
}
.box {
  position: relative;
  overflow: hidden;
  padding: 14px 16px;
  border-radius: var(--r-m);
  background: rgb(246 243 240 / 0.05);
}
.value {
  display: block;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 14px;
  color: var(--text);
  overflow-wrap: anywhere;
  filter: blur(7px);
  opacity: 0.55;
  user-select: none;
  transition:
    filter 420ms var(--ease-out),
    opacity 420ms var(--ease-out);
}
.veil {
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, rgb(124 106 174 / 0.18), rgb(110 140 196 / 0.12));
  clip-path: inset(0 0 0 0);
  transition: clip-path 520ms var(--ease);
}
.shown .value {
  filter: none;
  opacity: 1;
  user-select: all;
}
.shown .veil {
  clip-path: inset(0 0 0 100%);
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
</style>
