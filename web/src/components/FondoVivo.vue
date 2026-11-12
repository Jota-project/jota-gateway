<script setup lang="ts">
import { onBeforeUnmount, onMounted, useTemplateRef } from 'vue'
import { reducedMotion } from '@/lib/motion'

// El fondo vivo de Fogar en modo panel (fogar-brand, componente FondoVivo): humo de monte y brasa que se
// mueven despacio, en un Canvas de 112 × 64 escalado, con grano del 3 %. La brasa se suma (es luz) y no
// pasa de `brasa-tope-vidrio`. Sin bordes: se funde con la noche por una máscara radial. Con «reducir
// movimiento», queda quieto. `calor` sube o baja la brasa (0–1).
const props = withDefaults(defineProps<{ calor?: number; humo?: number; velocidad?: number }>(), {
  calor: 0.38,
  humo: 0.75,
  velocidad: 0.4,
})

const lienzo = useTemplateRef<HTMLCanvasElement>('lienzo')
let raf = 0

const P = {
  noche: [11, 16, 18],
  humo: [19, 32, 27],
  niebla: [32, 51, 42],
  rescoldo: [90, 26, 16],
  ascua: [184, 54, 26],
  llama: [238, 106, 44],
  chispa: [255, 225, 184],
  contra: [124, 199, 146],
} as const
const TOPE = 0.42
const rgba = (c: readonly number[], a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`
const mezcla = (a: readonly number[], b: readonly number[], t: number) => a.map((v, i) => Math.round(v + (b[i]! - v) * t))

function grano(): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = c.height = 128
  const x = c.getContext('2d')
  if (!x) return c
  const im = x.createImageData(128, 128)
  let s = 11
  for (let i = 0; i < im.data.length; i += 4) {
    s = (s * 16807) % 2147483647
    const v = s % 256
    im.data[i] = im.data[i + 1] = im.data[i + 2] = v
    im.data[i + 3] = 255
  }
  x.putImageData(im, 0, 0)
  return c
}

onMounted(() => {
  const cv = lienzo.value
  const c = cv?.getContext('2d')
  if (!cv || !c) return
  const LW = 112
  const LH = 64
  const off = document.createElement('canvas')
  off.width = LW
  off.height = LH
  const x = off.getContext('2d')
  if (!x) return
  const textura = grano()
  let patron: CanvasPattern | null = null
  const o = props

  function pinta(t: number) {
    if (!cv || !c || !x) return
    const dpr = Math.min(2, devicePixelRatio || 1) * 0.5
    const w = Math.round(cv.clientWidth * dpr)
    const h = Math.round(cv.clientHeight * dpr)
    if (w < 2 || h < 2) return
    if (cv.width !== w || cv.height !== h) {
      cv.width = w
      cv.height = h
      patron = null
    }
    const s = t * o.velocidad
    x.globalCompositeOperation = 'source-over'
    x.fillStyle = rgba(P.noche, 1)
    x.fillRect(0, 0, LW, LH)
    for (let k = 0; k < 3; k++) {
      const X = LW * (0.2 + 0.6 * (0.5 + 0.5 * Math.sin(s * 0.21 + k * 2.1)))
      const Y = LH * (0.15 + 0.55 * (0.5 + 0.5 * Math.sin(s * 0.17 + k * 1.3)))
      const r = LW * (0.42 + 0.08 * Math.sin(s * 0.3 + k))
      const g = x.createRadialGradient(X, Y, 0, X, Y, r)
      const col = k === 1 ? P.niebla : P.humo
      g.addColorStop(0, rgba(col, 0.75 * o.humo))
      g.addColorStop(1, rgba(col, 0))
      x.fillStyle = g
      x.fillRect(0, 0, LW, LH)
    }
    x.globalCompositeOperation = 'lighter'
    const X0 = LW * (0.5 + 0.45 * Math.sin(s * 0.09 + 1))
    const Y0 = LH * (0.25 + 0.2 * Math.sin(s * 0.13))
    const g0 = x.createRadialGradient(X0, Y0, 0, X0, Y0, LW * 0.5)
    g0.addColorStop(0, rgba(P.contra, 0.07 * o.humo))
    g0.addColorStop(1, rgba(P.contra, 0))
    x.fillStyle = g0
    x.fillRect(0, 0, LW, LH)
    for (let k = 0; k < 3; k++) {
      const X = LW * (0.18 + 0.64 * (0.5 + 0.5 * Math.sin(s * (0.11 + 0.04 * k) + k * 2.3)))
      const Y = LH * (0.8 + 0.18 * Math.sin(s * (0.23 + 0.05 * k) + k))
      const r = LW * (0.26 + 0.08 * Math.sin(s * 0.6 + k * 1.7))
      const fl = 0.8 + 0.2 * Math.sin(s * 1.3 + k * 2) * Math.sin(s * 0.41 + k)
      const a = o.calor * fl
      const tono = 0.5 + 0.5 * Math.sin(s * 0.35 + k * 2.1)
      const nucleo = mezcla(P.ascua, P.llama, tono)
      const g = x.createRadialGradient(X, Y, 0, X, Y, r)
      g.addColorStop(0, rgba(nucleo, TOPE * a))
      g.addColorStop(0.5, rgba(P.rescoldo, 0.28 * a))
      g.addColorStop(1, rgba(P.rescoldo, 0))
      x.fillStyle = g
      x.fillRect(0, 0, LW, LH)
      const g2 = x.createRadialGradient(X, Y, 0, X, Y, r * 0.4)
      g2.addColorStop(0, rgba(P.chispa, 0.1 * a * tono))
      g2.addColorStop(1, rgba(P.chispa, 0))
      x.fillStyle = g2
      x.fillRect(0, 0, LW, LH)
    }
    c.imageSmoothingEnabled = true
    c.imageSmoothingQuality = 'high'
    c.drawImage(off, 0, 0, w, h)
    if (!patron) patron = c.createPattern(textura, 'repeat')
    if (patron) {
      c.globalAlpha = 0.03
      c.fillStyle = patron
      c.fillRect(0, 0, w, h)
      c.globalAlpha = 1
    }
  }

  if (reducedMotion.value) {
    pinta(0)
    return
  }
  const bucle = (n: number) => {
    // Con la pestaña oculta el navegador para requestAnimationFrame por sí solo.
    pinta(n / 1000)
    raf = requestAnimationFrame(bucle)
  }
  raf = requestAnimationFrame(bucle)
})
onBeforeUnmount(() => cancelAnimationFrame(raf))
</script>

<template>
  <canvas ref="lienzo" class="fondo" aria-hidden="true" />
</template>

<style scoped>
.fondo {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  -webkit-mask-image: radial-gradient(closest-side, #000 55%, transparent 100%);
  mask-image: radial-gradient(closest-side, #000 55%, transparent 100%);
}
</style>
