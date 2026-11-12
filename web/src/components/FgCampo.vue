<script setup lang="ts">
import { useId } from 'vue'

// Campo de texto con su etiqueta, una pista y el error junto al campo (aria-invalid + aria-describedby).
const modelo = defineModel<string | number | null>({ default: '' })
defineProps<{
  etiqueta: string
  pista?: string
  error?: string | null
  type?: string
  placeholder?: string
  autocomplete?: string
  inputmode?: 'text' | 'decimal' | 'numeric'
  required?: boolean
  disabled?: boolean
}>()
const id = useId()
</script>

<template>
  <div class="campo">
    <label :for="id">{{ etiqueta }}</label>
    <input
      :id="id"
      v-model="modelo"
      :type="type ?? 'text'"
      :placeholder="placeholder"
      :autocomplete="autocomplete"
      :inputmode="inputmode"
      :required="required"
      :disabled="disabled"
      :aria-invalid="error ? 'true' : undefined"
      :aria-describedby="error || pista ? `${id}-nota` : undefined"
      spellcheck="false"
    />
    <p v-if="error" :id="`${id}-nota`" class="nota error" role="alert">{{ error }}</p>
    <p v-else-if="pista" :id="`${id}-nota`" class="nota">{{ pista }}</p>
  </div>
</template>

<style scoped>
.campo {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
label {
  font-size: 14px;
  font-weight: 550;
}
input {
  height: 44px;
  min-width: 0;
  padding: 0 14px;
  border-radius: var(--radio-nodo);
  border: 1px solid var(--veta);
  background: var(--noche);
  color: var(--hueso);
  font-size: 15px;
  transition: border-color var(--t-rapido) ease;
}
input:hover:not(:disabled) {
  border-color: #33403f;
}
input:focus-visible {
  outline: 2px solid var(--hueso);
  outline-offset: 1px;
  border-color: transparent;
}
input[aria-invalid='true'] {
  border-color: var(--error);
}
.nota {
  margin: 0;
  font-size: 13px;
  color: var(--hueso-tenue);
}
.nota.error {
  color: var(--hueso);
}
.nota.error::before {
  content: '';
  display: inline-block;
  width: 12px;
  height: 11px;
  margin-right: 7px;
  vertical-align: -1px;
  background: var(--error);
  clip-path: polygon(50% 0, 100% 100%, 0 100%);
}
</style>
