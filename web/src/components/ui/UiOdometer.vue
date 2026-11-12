<script setup lang="ts">
import { computed } from 'vue'

// Cifra que rueda: cada dígito es una columna 0–9 que se desplaza al valor nuevo.
// Formato es-ES (sin separador en cuatro cifras, como pide la RAE: 1284; con él desde cinco: 12.840).
const props = defineProps<{ value: number }>()

const format = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 0 })
const text = computed(() => format.format(props.value))
const chars = computed(() => [...text.value])
const DIGITS = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']
</script>

<template>
  <span class="odo tab" role="img" :aria-label="text">
    <template v-for="(char, i) in chars" :key="chars.length - i">
      <span v-if="/\d/.test(char)" class="digit" aria-hidden="true">
        <span class="col" :style="{ transform: `translateY(-${char}em)` }">
          <span v-for="d in DIGITS" :key="d">{{ d }}</span>
        </span>
      </span>
      <span v-else class="sep" aria-hidden="true">{{ char }}</span>
    </template>
  </span>
</template>

<style scoped>
.odo {
  display: inline-flex;
  line-height: 1;
}
.digit {
  display: inline-block;
  width: 0.6em;
  height: 1em;
  overflow: hidden;
  text-align: center;
}
.col {
  display: flex;
  flex-direction: column;
  transition: transform 620ms var(--ease);
}
.col > span {
  display: block;
  height: 1em;
}
.sep {
  width: 0.28em;
  text-align: center;
}
</style>
