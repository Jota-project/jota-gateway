<script setup lang="ts" generic="T extends string">
import { useId, useTemplateRef } from 'vue'
import AppIcon, { type IconName } from '@/components/AppIcon.vue'
import { replay } from '@/lib/motion'

// Varias opciones a la vez, con un mínimo (p. ej. cómo responde un cliente: voz, texto y estado).
// Si intentas quitar la última permitida, la tarjeta dice que no y la nota se ilumina.
const props = withDefaults(
  defineProps<{
    options: { value: T; label: string; hint: string; icon: IconName }[]
    label: string
    note?: string
    min?: number
  }>(),
  { min: 1, note: undefined },
)
const model = defineModel<T[]>({ required: true })

const id = useId()
const noteEl = useTemplateRef<HTMLElement>('noteEl')

function toggle(value: T, event: MouseEvent): void {
  const on = model.value.includes(value)
  if (on && model.value.length <= props.min) {
    replay(event.currentTarget as HTMLElement)
    replay(noteEl.value, 'j-flash')
    return
  }
  model.value = on ? model.value.filter((v) => v !== value) : [...model.value, value]
}
</script>

<template>
  <div class="tiles-wrap">
    <span :id="`${id}-label`" class="label">{{ props.label }}</span>
    <div class="tiles" role="group" :aria-labelledby="`${id}-label`" :aria-describedby="props.note ? `${id}-note` : undefined">
      <button
        v-for="option in props.options"
        :key="option.value"
        type="button"
        class="tile"
        :aria-pressed="model.includes(option.value)"
        @click="toggle(option.value, $event)"
      >
        <span class="tick" aria-hidden="true"><AppIcon name="check" :size="13" /></span>
        <AppIcon :name="option.icon" class="icon" />
        <b>{{ option.label }}</b>
        <span class="hint">{{ option.hint }}</span>
      </button>
    </div>
    <span v-if="props.note" :id="`${id}-note`" ref="noteEl" class="note">{{ props.note }}</span>
  </div>
</template>

<style scoped>
.tiles-wrap {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.label {
  font-size: 14px;
  font-weight: 550;
  color: var(--text-soft);
}
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}
.tile {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 14px;
  border: 0;
  border-radius: var(--r-m);
  background: rgb(246 243 240 / 0.05);
  color: var(--text-soft);
  text-align: left;
  cursor: pointer;
  box-shadow: inset 0 0 0 1px transparent;
  transition:
    background-color var(--t-quick) ease,
    box-shadow var(--t-base) var(--ease-out),
    transform var(--t-press) var(--ease-out);
}
.tile:active {
  transform: scale(0.97);
}
.tile b {
  margin-top: 6px;
  font-weight: 600;
  color: var(--text);
}
.hint {
  font-size: 12.5px;
  color: var(--text-faint);
}
.icon {
  color: var(--text-faint);
  transition: color var(--t-quick) ease;
}
.tile[aria-pressed='true'] {
  background: var(--accent-tint);
  box-shadow: inset 0 0 0 1px rgb(210 201 238 / 0.28);
}
.tile[aria-pressed='true'] .icon {
  color: var(--accent-text);
}
.tick {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  opacity: 0;
  transform: scale(0.5);
  transition:
    opacity var(--t-quick) ease,
    transform var(--t-scene) var(--spring);
}
.tick :deep(svg) {
  stroke-width: 2.6;
}
.tile[aria-pressed='true'] .tick {
  opacity: 1;
  transform: none;
}
.note {
  font-size: 13px;
  color: var(--text-faint);
}
@media (max-width: 520px) {
  .tiles {
    grid-template-columns: minmax(0, 1fr);
  }
  .tile {
    flex-direction: row;
    align-items: center;
    gap: 10px;
  }
  .tile b {
    margin: 0;
  }
  .tick {
    top: 50%;
    margin-top: -10px;
  }
}
</style>
