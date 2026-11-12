<script setup lang="ts">
import { useTemplateRef } from 'vue'
import AppIcon from '@/components/AppIcon.vue'
import { replay } from '@/lib/motion'
import UiReveal from './UiReveal.vue'

// Campo de texto con etiqueta, ayuda y error. El error abre su línea debajo; `nudge()` hace el «no» corto
// (llámalo al enviar con errores, no al escribir). Los atributos (type, inputmode, placeholder…) van al input.
defineOptions({ inheritAttrs: false })
const props = defineProps<{ id: string; label: string; error?: string; hint?: string }>()
const model = defineModel<string>({ default: '' })
const emit = defineEmits<{ blur: [] }>()

const input = useTemplateRef<HTMLInputElement>('input')
defineExpose({
  nudge: () => replay(input.value),
  focus: () => input.value?.focus(),
})
</script>

<template>
  <div class="field" :class="{ invalid: props.error }">
    <label :for="props.id">{{ props.label }}</label>
    <input
      :id="props.id"
      ref="input"
      v-model="model"
      v-bind="$attrs"
      class="input"
      :aria-invalid="Boolean(props.error)"
      :aria-describedby="[props.error ? `${props.id}-error` : '', props.hint ? `${props.id}-hint` : ''].join(' ').trim() || undefined"
      @blur="emit('blur')"
    >
    <UiReveal :open="Boolean(props.error)">
      <p :id="`${props.id}-error`" class="error" role="alert">
        <AppIcon name="aviso" :size="16" />
        <span>{{ props.error }}</span>
      </p>
    </UiReveal>
    <span v-if="props.hint" :id="`${props.id}-hint`" class="hint">{{ props.hint }}</span>
  </div>
</template>

<style scoped>
.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
label {
  font-size: 14px;
  font-weight: 550;
  color: var(--text-soft);
}
.input {
  height: 46px;
  padding: 0 16px;
  border: 0;
  border-radius: var(--r-m);
  outline: 0;
  background: rgb(246 243 240 / 0.06);
  color: var(--text);
  box-shadow:
    inset 0 0 0 1px transparent,
    0 0 0 0 rgb(124 106 174 / 0);
  transition:
    box-shadow var(--t-base) var(--ease-out),
    background-color var(--t-quick) ease;
}
.input:focus {
  background: rgb(246 243 240 / 0.08);
  box-shadow:
    inset 0 0 0 1px var(--accent-text),
    0 0 0 5px rgb(124 106 174 / 0.22);
}
.invalid .input {
  box-shadow:
    inset 0 0 0 1px var(--bad),
    0 0 0 5px rgb(239 138 124 / 0.14);
}
.error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 0;
  padding-top: 2px;
  font-size: 13.5px;
  color: var(--bad);
}
.error :deep(svg) {
  margin-top: 2px;
  flex: none;
}
.hint {
  font-size: 13px;
  color: var(--text-faint);
}
</style>
