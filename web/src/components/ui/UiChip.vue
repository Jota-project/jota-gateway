<script setup lang="ts">
import AppIcon from '@/components/AppIcon.vue'

// Filtro que se activa y se desactiva. El check abre su propio hueco en vez de aparecer de golpe.
const props = defineProps<{ count?: number }>()
const pressed = defineModel<boolean>({ default: false })
</script>

<template>
  <button type="button" class="chip" :aria-pressed="pressed" @click="pressed = !pressed">
    <span class="tick" aria-hidden="true"><span><AppIcon name="check" :size="16" /></span></span>
    <slot />
    <span v-if="props.count !== undefined" class="count tab">{{ props.count }}</span>
  </button>
</template>

<style scoped>
.chip {
  display: inline-flex;
  align-items: center;
  height: 34px;
  padding: 0 14px;
  border: 0;
  border-radius: 999px;
  background: rgb(246 243 240 / 0.06);
  color: var(--text-soft);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color var(--t-quick) ease,
    color var(--t-quick) ease,
    transform var(--t-press) var(--ease-out);
}
.chip:active {
  transform: scale(0.96);
}
.tick {
  display: grid;
  grid-template-columns: 0fr;
  transition:
    grid-template-columns var(--t-base) var(--ease-out),
    margin var(--t-base) var(--ease-out);
}
.tick > span {
  display: flex;
  overflow: hidden;
}
.tick :deep(svg) {
  stroke-width: 2.2;
}
.chip[aria-pressed='true'] {
  background: var(--accent-tint);
  color: var(--text);
}
.chip[aria-pressed='true'] .tick {
  grid-template-columns: 1fr;
  margin-right: 6px;
}
.count {
  margin-left: 7px;
  font-size: 13px;
  color: var(--text-faint);
}
</style>
