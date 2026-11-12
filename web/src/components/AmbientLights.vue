<script setup lang="ts">
// Las dos luces de JScreen con significado: malva y cielo si todo va bien; la de cielo pasa a ámbar cuando
// algo falla. El cambio es un fundido de opacidad entre dos capas (los degradados no se interpolan bien).
// Solo se mueve `transform` (órbitas de 131 y 191 s) y la opacidad.
defineProps<{ warn?: boolean }>()
</script>

<template>
  <div class="ambient" :class="{ warn }" aria-hidden="true">
    <div class="light malva" />
    <div class="light cielo" />
    <div class="light ambar" />
  </div>
</template>

<style scoped>
.ambient {
  position: fixed;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  z-index: 0;
}
.light {
  position: absolute;
  border-radius: 50%;
  transition: opacity 1.6s var(--ease);
}
.malva {
  width: 780px;
  height: 780px;
  top: -320px;
  left: 28%;
  background: radial-gradient(circle, rgb(124 106 174 / var(--orb)), transparent 64%);
  animation: orbit-a 131s linear infinite;
}
.cielo,
.ambar {
  width: 720px;
  height: 720px;
  bottom: -360px;
  right: -160px;
  animation: orbit-b 191s linear infinite;
}
.cielo {
  background: radial-gradient(circle, rgb(110 140 196 / 0.2), transparent 64%);
}
.ambar {
  background: radial-gradient(circle, rgb(232 178 106 / 0.22), transparent 64%);
  opacity: 0;
}
.warn .cielo {
  opacity: 0;
}
.warn .ambar {
  opacity: 1;
}
@keyframes orbit-a {
  to {
    transform: rotate(360deg) translateX(70px) rotate(-360deg);
  }
}
@keyframes orbit-b {
  to {
    transform: rotate(-360deg) translateX(90px) rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .light {
    animation: none;
  }
}
</style>
