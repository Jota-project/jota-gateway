<script lang="ts">
// Estado con su punto. Conectado se queda encendido y quieto (solo el orbe y las luces laten); reconectando gira; caído se queda quieto (lo roto no
// debe parecer vivo); desactivado (`off`) es un aro hueco; esperando late más deprisa, en malva. El texto cambia con un fundido desenfocado.
export type StatusTone = 'ok' | 'warn' | 'bad' | 'waiting' | 'off'
</script>

<script setup lang="ts">
const props = withDefaults(defineProps<{ tone?: StatusTone; label: string; size?: 'md' | 'sm' }>(), {
  tone: 'ok',
  size: 'md',
})
</script>

<template>
  <span class="status" :class="[props.tone, props.size]">
    <span class="dot" aria-hidden="true" />
    <span class="text">
      <Transition name="j-swap"><span :key="props.label">{{ props.label }}</span></Transition>
    </span>
  </span>
</template>

<style scoped>
.status {
  --c: var(--status);
  display: inline-flex;
  align-items: center;
  gap: 9px;
  color: var(--text-soft);
  transition: color var(--t-base) ease;
}
.text {
  position: relative;
  display: inline-flex;
}
.dot {
  position: relative;
  width: 10px;
  height: 10px;
  flex: none;
}
.sm .dot {
  width: 7px;
  height: 7px;
}
.sm {
  gap: 7px;
  font-size: 12.5px;
  color: var(--text-faint);
}
.dot::before,
.dot::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: var(--c);
  transition: background-color var(--t-base) ease;
}
.dot::after {
  opacity: 0;
}
.warn {
  --c: var(--warn);
  color: var(--warn);
}
.warn .dot::after {
  inset: -4px;
  opacity: 1;
  background: transparent;
  border: 2px solid var(--warn);
  border-right-color: transparent;
  border-bottom-color: transparent;
  animation: j-spin 1s linear infinite;
}
.bad {
  --c: var(--bad);
  color: var(--bad);
}
.off {
  color: var(--text-faint);
}
.off .dot::before {
  background: transparent;
  box-shadow: inset 0 0 0 1.5px var(--text-faint);
}
.waiting {
  --c: var(--accent-text);
}
.waiting .dot::after {
  animation: j-breath 1.8s var(--ease-in-out) infinite;
}
</style>
