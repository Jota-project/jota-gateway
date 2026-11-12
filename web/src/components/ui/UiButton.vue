<script setup lang="ts">
import { onBeforeUpdate, onUpdated, useTemplateRef } from 'vue'
import AppIcon, { type IconName } from '@/components/AppIcon.vue'
import { useFlipWidth } from '@/lib/motion'

// Botón de Jota Dusk: píldora que se hunde al pulsar. Cuando cambia `stateKey` (comprobando, hecho…),
// el contenido viejo sale desenfocado, entra el nuevo y el ancho se ajusta sin saltos.
const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'quiet' | 'ghost'
    size?: 'md' | 'sm'
    icon?: IconName
    /** Sin texto visible: pasa `aria-label` como atributo. */
    iconOnly?: boolean
    busy?: boolean
    stateKey?: string | number
    type?: 'button' | 'submit'
  }>(),
  { variant: 'quiet', size: 'md', type: 'button', stateKey: 0, icon: undefined },
)

const el = useTemplateRef<HTMLButtonElement>('el')
const flip = useFlipWidth()
onBeforeUpdate(() => flip.before(el.value))
onUpdated(() => flip.after(el.value))
</script>

<template>
  <button
    ref="el"
    :type="props.type"
    class="btn"
    :class="[props.variant, props.size, { 'icon-only': props.iconOnly }]"
    :aria-busy="props.busy || undefined"
  >
    <Transition name="j-swap">
      <span :key="props.stateKey" class="content">
        <span v-if="props.busy" class="j-spinner" aria-hidden="true" />
        <AppIcon v-else-if="props.icon" :name="props.icon" :size="props.size === 'sm' ? 18 : 20" />
        <slot />
      </span>
    </Transition>
  </button>
</template>

<style scoped>
.btn {
  --btn-bg: rgb(246 243 240 / 0.07);
  --btn-fg: var(--text);
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 44px;
  padding: 0 20px;
  border: 0;
  border-radius: 999px;
  background: var(--btn-bg);
  color: var(--btn-fg);
  font-size: 15px;
  font-weight: 550;
  white-space: nowrap;
  cursor: pointer;
  overflow: hidden;
  -webkit-tap-highlight-color: transparent;
  user-select: none;
  transition:
    transform var(--t-press) var(--ease-out),
    background-color var(--t-quick) ease,
    box-shadow var(--t-base) var(--ease-out),
    color var(--t-quick) ease;
}
.btn:active:not(:disabled) {
  transform: scale(0.97);
}
.btn:disabled {
  opacity: 0.42;
  cursor: not-allowed;
}
.content {
  display: inline-flex;
  align-items: center;
  gap: 9px;
}

.primary {
  --btn-bg: var(--accent);
  --btn-fg: var(--on-accent);
  box-shadow:
    0 10px 26px -8px rgb(60 40 110 / 0.55),
    inset 0 1px 0 rgb(255 255 255 / 0.14);
}
.ghost {
  --btn-bg: transparent;
  box-shadow: inset 0 0 0 1px var(--line);
}
@media (hover: hover) and (pointer: fine) {
  .primary:hover:not(:disabled) {
    --btn-bg: var(--accent-hover);
    box-shadow:
      0 14px 32px -8px rgb(60 40 110 / 0.7),
      inset 0 1px 0 rgb(255 255 255 / 0.18);
  }
  .quiet:hover:not(:disabled) {
    --btn-bg: rgb(246 243 240 / 0.1);
  }
  .ghost:hover:not(:disabled) {
    --btn-fg: var(--accent-text);
    box-shadow: inset 0 0 0 1px var(--accent-text);
  }
}

.sm {
  height: 36px;
  padding: 0 14px;
  font-size: 13.5px;
}
.icon-only {
  width: 44px;
  padding: 0;
}
.sm.icon-only {
  width: 36px;
}
</style>
