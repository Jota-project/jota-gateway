<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue'
import { reducedMotion } from '@/lib/motion'

// El código de emparejamiento tal como lo enseña la web (POST /admin/pairing): seis cifras en grande que se
// barajan y se asientan una a una, y una línea que se consume hasta que caduca. `folded` pliega las cifras
// cuando el código se ha usado. Emite `expired` al llegar a cero.
const props = withDefaults(defineProps<{ code: string; expiresAt: string; ttlSeconds?: number; folded?: boolean }>(), {
  ttlSeconds: 600,
  folded: false,
})
const emit = defineEmits<{ expired: [] }>()

const shown = ref<string[]>([])
const settled = ref<boolean[]>([])
const left = ref(0)
const bar = useTemplateRef<HTMLElement>('bar')

const digits = computed(() => [...props.code])
const spoken = computed(() => `${props.code.slice(0, 3)} ${props.code.slice(3)}`)
const clock = computed(() => `${Math.floor(left.value / 60)}:${String(Math.max(0, left.value) % 60).padStart(2, '0')}`)

let frame = 0
function shuffle(): void {
  cancelAnimationFrame(frame)
  const target = digits.value
  if (reducedMotion.value) {
    shown.value = [...target]
    settled.value = target.map(() => true)
    return
  }
  settled.value = target.map(() => false)
  const t0 = performance.now()
  const step = (t: number): void => {
    // Cada cifra se baraja un momento y se asienta, de izquierda a derecha.
    shown.value = target.map((d, i) => {
      if (t - t0 > 260 + i * 90) {
        settled.value[i] = true
        return d
      }
      return String(Math.floor(Math.random() * 10))
    })
    if (settled.value.some((s) => !s)) frame = requestAnimationFrame(step)
  }
  frame = requestAnimationFrame(step)
}

let tick: ReturnType<typeof setInterval> | undefined
function countdown(): void {
  clearInterval(tick)
  const end = new Date(props.expiresAt).getTime()
  const update = (): void => {
    left.value = Math.max(0, Math.round((end - Date.now()) / 1000))
    if (left.value === 0) {
      clearInterval(tick)
      emit('expired')
    }
  }
  update()
  tick = setInterval(update, 1000)
  // La línea arranca donde toca (si el código ya llevaba tiempo abierto) y se consume a ritmo constante.
  const el = bar.value
  if (!el) return
  el.getAnimations().forEach((a) => a.cancel())
  const from = Math.min(1, left.value / props.ttlSeconds)
  el.animate([{ transform: `scaleX(${from})` }, { transform: 'scaleX(0)' }], {
    duration: left.value * 1000,
    easing: 'linear',
    fill: 'forwards',
  })
}

watch(() => props.code, shuffle)
watch(() => props.expiresAt, countdown)
onMounted(() => {
  shuffle()
  countdown()
})
onBeforeUnmount(() => {
  cancelAnimationFrame(frame)
  clearInterval(tick)
})
</script>

<template>
  <div class="pairing">
    <div class="code display" :class="{ folded: props.folded }" role="text" :aria-label="`Código ${spoken}`">
      <template v-for="(d, i) in shown" :key="i">
        <span v-if="i === 3" class="gap" aria-hidden="true" />
        <span class="digit" :class="{ settled: settled[i] }" :style="{ '--i': i }" aria-hidden="true">{{ d }}</span>
      </template>
    </div>
    <div class="ttl" :class="{ low: left <= 60 }" aria-hidden="true"><i ref="bar" /></div>
    <span class="left tab">Caduca en {{ clock }}</span>
  </div>
</template>

<style scoped>
.pairing {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.code {
  display: flex;
  align-items: center;
  font-size: clamp(56px, 8vw, 84px);
  line-height: 1;
  font-weight: 330;
  letter-spacing: 0;
}
.digit {
  display: inline-block;
  width: 0.62em;
  text-align: center;
  color: var(--text-faint);
  transition:
    color var(--t-quick) ease,
    opacity 260ms ease,
    transform 380ms var(--ease-out),
    filter 260ms ease;
  transition-delay: calc(var(--i) * 40ms);
}
.digit.settled {
  color: var(--text);
  animation: j-blur-in 320ms var(--ease-out);
}
.gap {
  width: 0.32em;
}
.folded .digit {
  opacity: 0;
  transform: translateY(-0.25em) scale(0.9);
  filter: blur(6px);
}
.ttl {
  width: min(100%, 420px);
  height: 3px;
  overflow: hidden;
  border-radius: 999px;
  background: rgb(246 243 240 / 0.08);
}
.ttl i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent-text);
  transform-origin: left;
  transition: background-color 600ms ease;
}
.ttl.low i {
  background: var(--warn);
}
.left {
  font-size: 13px;
  color: var(--text-faint);
}
</style>
