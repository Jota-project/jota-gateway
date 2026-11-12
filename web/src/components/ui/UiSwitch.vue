<script setup lang="ts">
// Interruptor: la bola viaja con muelle y, al pulsar, se estira hacia donde va a ir.
const on = defineModel<boolean>({ default: false })
</script>

<template>
  <button type="button" role="switch" class="switch" :aria-checked="on" @click="on = !on">
    <span class="track" aria-hidden="true"><span class="thumb" /></span>
    <span class="label"><slot /></span>
  </button>
</template>

<style scoped>
.switch {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
  border: 0;
  background: none;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.track {
  position: relative;
  flex: none;
  width: 46px;
  height: 28px;
  border-radius: 999px;
  background: var(--switch-off);
  transition: background-color var(--t-base) ease;
}
.thumb {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 22px;
  height: 22px;
  border-radius: 999px;
  background: #fff;
  box-shadow: 0 2px 6px rgb(10 8 14 / 0.35);
  transition:
    transform var(--t-scene) var(--spring),
    width var(--t-quick) var(--ease-out);
}
[aria-checked='true'] .track {
  background: var(--accent);
}
[aria-checked='true'] .thumb {
  transform: translateX(18px);
}
.switch:active .thumb {
  width: 27px;
}
.switch[aria-checked='true']:active .thumb {
  transform: translateX(13px);
}
</style>
