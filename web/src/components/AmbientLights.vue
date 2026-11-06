<script setup lang="ts">
// Las dos luces de JScreen con significado: malva y cielo si todo va bien; la de cielo
// pasa a ámbar cuando algo falla. Solo se mueven `transform` y el color (4 s, t-color).
defineProps<{ warn?: boolean }>()
</script>

<template>
  <div class="ambient" aria-hidden="true">
    <div class="light malva" />
    <div class="light cielo" :class="{ warn }" />
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
  transition: background 4s linear;
}

.malva {
  width: 780px;
  height: 780px;
  top: -320px;
  left: 28%;
  background: radial-gradient(circle, rgb(124 106 174 / var(--orb)), transparent 64%);
  animation: orbit-a 131s linear infinite;
}

.cielo {
  width: 720px;
  height: 720px;
  bottom: -360px;
  right: -160px;
  background: radial-gradient(circle, rgb(110 140 196 / 0.2), transparent 64%);
  animation: orbit-b 191s linear infinite;
}

.cielo.warn {
  background: radial-gradient(circle, rgb(232 178 106 / 0.2), transparent 64%);
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
</style>
