<script setup lang="ts">
import { onBeforeUnmount, onMounted, useTemplateRef, watch } from 'vue'
import { createLineaViva, type LineaViva, type LineaVivaState } from '@/lib/lineaViva'
import { reducedMotion } from '@/lib/motion'

// La marca en la interfaz: la línea que escribe «Fogar» y cambia de forma con el estado.
// El lienzo es de 1000 × 420 (la proporción del motor); la palabra ocupa el 80 % central del ancho.
const props = withDefaults(defineProps<{ estado?: LineaVivaState; etiqueta?: string }>(), {
  estado: 'rest',
  etiqueta: 'Fogar',
})

const lienzo = useTemplateRef<HTMLCanvasElement>('lienzo')
let linea: LineaViva | null = null
// La pose cambia como mucho una vez cada 2 s: el estado vivo llega a 1 Hz y no debe hacer tiritar la línea.
const CALMA_MS = 2000
let ultimo = 0
let pendiente: ReturnType<typeof setTimeout> | undefined

function aplicar(estado: LineaVivaState) {
  clearTimeout(pendiente)
  const espera = ultimo + CALMA_MS - Date.now()
  if (espera > 0) {
    pendiente = setTimeout(() => aplicar(props.estado), espera)
    return
  }
  ultimo = Date.now()
  linea?.setState(estado)
}

onMounted(() => {
  if (!lienzo.value) return
  linea = createLineaViva(lienzo.value, { reduce: reducedMotion.value })
  ultimo = Date.now()
  if (props.estado !== 'rest') aplicar(props.estado)
})
watch(() => props.estado, (estado) => aplicar(estado))
onBeforeUnmount(() => {
  clearTimeout(pendiente)
  linea?.destroy()
})
</script>

<template>
  <canvas ref="lienzo" class="linea" role="img" :aria-label="etiqueta" />
</template>

<style scoped>
.linea {
  display: block;
  width: 100%;
  aspect-ratio: 1000 / 420;
}
</style>
