<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue'
import type { GatewayState, LiveSession } from '@/api/types'
import { formatDuration } from '@/lib/format'
import { serviceTone } from '@/lib/health'
import { label, PROVIDER } from '@/lib/labels'
import type { LineaVivaState } from '@/lib/lineaViva'
import FgEstado, { type EstadoTono } from './FgEstado.vue'
import FondoVivo from './FondoVivo.vue'
import LineaViva from './LineaViva.vue'

// Quién habla con quién: los aparatos conectados a la izquierda, el gateway (la línea viva) en el centro
// y los servicios a la derecha. Cada hilo llega a su propio puerto junto a la palabra, en el orden de los
// nodos, así que no se cruzan. Solo el hilo de un turno abierto lleva la brasa (el foco de lo que pasa
// ahora); se enciende con un fundido y se queda al menos 2 s. Un servicio que no responde va punteado.
const props = defineProps<{ state: GatewayState; detalles: Partial<Record<'orchestrator' | 'stt' | 'tts', { reconnect_attempts: number }>> }>()

const MAX_APARATOS = 4
const HOLD_MS = 2000
const encendidas = new Map<string, number>()
const ahora = ref(Date.now())

watch(
  () => props.state,
  (s) => {
    ahora.value = Date.now()
    for (const row of s.sessions) if (row.turn.kind !== 'idle') encendidas.set(row.session_id, ahora.value + HOLD_MS)
  },
  { immediate: true },
)
const encendida = (row: LiveSession) => (encendidas.get(row.session_id) ?? 0) > ahora.value

const aparatos = computed(() => {
  // Orden estable (la más antigua arriba): que los nodos no bailen cada segundo.
  const rows = [...props.state.sessions].sort((a, b) => b.age_s - a.age_s || a.session_id.localeCompare(b.session_id))
  return {
    visibles: rows.slice(0, MAX_APARATOS).map((row) => ({
      row,
      viva: encendida(row),
      ahora:
        row.turn.kind === 'idle'
          ? 'en espera'
          : `${row.turn.kind === 'push' ? 'Fogar habla' : 'turno abierto'}${row.turn.since_s != null ? ` · ${formatDuration(row.turn.since_s)}` : ''}`,
      ultimo: row.last_latencies?.turn_e2e_ms != null ? (row.last_latencies.turn_e2e_ms / 1000).toLocaleString('es-ES', { maximumFractionDigits: 1 }) : null,
    })),
    resto: Math.max(0, rows.length - MAX_APARATOS),
  }
})

const algunaViva = computed(() => aparatos.value.visibles.some((a) => a.viva))
const poseLinea = computed<LineaVivaState>(() => {
  if (props.state.sessions.some((s) => s.turn.kind === 'push')) return 'speak'
  return props.state.sessions.some((s) => s.turn.kind === 'normal') ? 'think' : 'rest'
})
const abiertos = computed(() => props.state.sessions.filter((s) => s.turn.kind !== 'idle').length)

const servicios = computed(() =>
  (['orchestrator', 'stt', 'tts'] as const).map((key) => {
    const snap = props.state.services[key]
    const tono = serviceTone(snap)
    const intentos = props.detalles[key]?.reconnect_attempts
    const proveedor = 'provider' in snap ? label(PROVIDER, snap.provider) : 'Orquestador'
    const nombre = { orchestrator: 'OpenClaw', stt: 'Transcripción', tts: 'Voz' }[key]
    const texto =
      tono === 'off'
        ? 'apagada en la configuración'
        : tono === 'ok'
          ? `${proveedor} · conectado`
          : `${proveedor} · ${tono === 'warn' ? 'reconectando' : 'no responde'}${intentos ? `, ${intentos} intentos` : ''}`
    const estado: EstadoTono = tono
    return { key, nombre, texto, estado, caido: tono !== 'ok' }
  }),
)

// Geometría de los hilos, medida en el DOM (los nodos son flex, no posiciones fijas).
const caja = useTemplateRef<HTMLDivElement>('caja')
const hub = useTemplateRef<HTMLDivElement>('hub')
const hilos = ref<{ d: string; viva: boolean; caido: boolean; puerto: [number, number] }[]>([])
const tam = ref({ w: 0, h: 0 })

function medir() {
  const c = caja.value
  const h = hub.value
  if (!c || !h) return
  const base = c.getBoundingClientRect()
  const hb = h.getBoundingClientRect()
  tam.value = { w: base.width, h: base.height }
  const cy = hb.top + hb.height / 2 - base.top
  const izq = hb.left - base.left - 12
  const der = hb.right - base.left + 12
  const curva = (x1: number, y1: number, x2: number, y2: number) => {
    const m = (x1 + x2) / 2
    return `M${x1} ${y1} C ${m} ${y1}, ${m} ${y2}, ${x2} ${y2}`
  }
  const out: typeof hilos.value = []
  const lado = (sel: string, entra: boolean) => {
    const nodos = [...c.querySelectorAll<HTMLElement>(sel)]
    nodos.forEach((n, i) => {
      const r = n.getBoundingClientRect()
      const y = r.top + r.height / 2 - base.top
      const py = cy + (i - (nodos.length - 1) / 2) * 12
      const viva = n.dataset.viva === 'true'
      const caido = n.dataset.caido === 'true'
      if (entra) out.push({ d: curva(r.right - base.left, y, izq, py), viva, caido, puerto: [izq, py] })
      else out.push({ d: curva(der, py, r.left - base.left, y), viva, caido, puerto: [der, py] })
    })
  }
  lado('.aparato', true)
  lado('.servicio', false)
  hilos.value = out
}

let ro: ResizeObserver | undefined
onMounted(() => {
  void nextTick(medir)
  ro = new ResizeObserver(() => medir())
  if (caja.value) ro.observe(caja.value)
})
watch([aparatos, servicios], () => void nextTick(medir))
onBeforeUnmount(() => ro?.disconnect())

// El hilo de OpenClaw se enciende con cualquier turno abierto.
const vivaServicio = (key: string) => key === 'orchestrator' && algunaViva.value
</script>

<template>
  <div ref="caja" class="lienzo">
    <FondoVivo :calor="algunaViva ? 0.5 : 0.3" />
    <svg class="hilos" :viewBox="`0 0 ${tam.w} ${tam.h}`" aria-hidden="true">
      <defs>
        <linearGradient id="brasa-hilo" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0" stop-color="#b8361a" /><stop offset=".55" stop-color="#ee6a2c" /><stop offset="1" stop-color="#ffe1b8" />
        </linearGradient>
      </defs>
      <g v-for="(h, i) in hilos" :key="i">
        <path :d="h.d" class="hilo" :class="{ caido: h.caido }" />
        <path :d="h.d" class="brasa" :class="{ on: h.viva }" />
        <circle :cx="h.puerto[0]" :cy="h.puerto[1]" r="2.5" class="puerto" :class="{ on: h.viva }" />
      </g>
    </svg>

    <div class="columna">
      <p v-if="!aparatos.visibles.length" class="vacio fg-meta">Ningún aparato conectado ahora.</p>
      <RouterLink
        v-for="a in aparatos.visibles"
        :key="a.row.session_id"
        :to="`/sesiones/${encodeURIComponent(a.row.session_id)}`"
        class="nodo aparato"
        :class="{ viva: a.viva }"
        :data-viva="a.viva"
      >
        <span class="nombre">{{ a.row.client_name }}</span>
        <span class="fg-meta">{{ a.ahora }}<template v-if="a.ultimo"> · último turno {{ a.ultimo }} s</template></span>
      </RouterLink>
      <RouterLink v-if="aparatos.resto" to="/sesiones" class="fg-meta mas">y {{ aparatos.resto }} más</RouterLink>
    </div>

    <div ref="hub" class="hub">
      <LineaViva :estado="poseLinea" :etiqueta="`Fogar, ${abiertos ? `${abiertos} turno${abiertos === 1 ? '' : 's'} abierto${abiertos === 1 ? '' : 's'}` : 'en reposo'}`" />
      <span class="fg-meta">Gateway · {{ abiertos ? `${abiertos} turno${abiertos === 1 ? '' : 's'} abierto${abiertos === 1 ? '' : 's'}` : 'en reposo' }}</span>
    </div>

    <div class="columna">
      <div v-for="s in servicios" :key="s.key" class="nodo servicio" :data-viva="vivaServicio(s.key)" :data-caido="s.caido">
        <FgEstado :tono="s.estado" :texto="s.nombre" class="nombre" />
        <span class="fg-meta">{{ s.texto }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lienzo {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(150px, 0.7fr) minmax(0, 1fr);
  align-items: center;
  gap: 24px;
  min-height: 340px;
  padding: 32px 0;
}
.lienzo :deep(.fondo) {
  inset: -60px -40px;
  width: calc(100% + 80px);
  height: calc(100% + 120px);
}
.hilos {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
  pointer-events: none;
}
.hilo {
  fill: none;
  stroke: var(--hilo);
  stroke-width: 1.5;
}
.hilo.caido {
  stroke: var(--hilo-caido);
  stroke-dasharray: 5 6;
}
.brasa {
  fill: none;
  stroke: url(#brasa-hilo);
  stroke-width: 2.5;
  opacity: 0;
  transition: opacity 300ms var(--ease-out);
}
.brasa.on {
  opacity: 1;
}
.puerto {
  fill: var(--hilo-caido);
  transition: fill 300ms ease;
}
.puerto.on {
  fill: var(--chispa);
}
.columna {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
.nodo {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-height: 56px;
  justify-content: center;
  padding: 8px 14px;
  border-radius: var(--radio-nodo);
  background: var(--vidrio-fino);
  -webkit-backdrop-filter: blur(14px) saturate(1.2);
  backdrop-filter: blur(14px) saturate(1.2);
  box-shadow: var(--vidrio-fino-borde);
  transition: box-shadow 300ms ease;
}
.nodo .nombre {
  font-weight: 600;
}
a.nodo:hover {
  box-shadow: inset 0 1px 0 rgb(236 230 220 / 0.12), inset 0 0 0 1px rgb(236 230 220 / 0.18);
}
.aparato.viva {
  box-shadow: inset 0 1px 0 rgb(236 230 220 / 0.1), inset 0 0 0 1px rgb(238 106 44 / 0.42);
}
.mas,
.vacio {
  position: relative;
  padding: 0 14px;
}
.hub {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  justify-self: center;
  width: 160px;
}
.hub .fg-meta {
  white-space: nowrap;
}

@media (max-width: 760px) {
  .lienzo {
    grid-template-columns: 1fr;
    gap: 10px;
    padding: 12px 0;
    min-height: 0;
  }
  .hilos {
    display: none;
  }
  .hub {
    flex-direction: row;
    justify-self: start;
    width: auto;
    gap: 12px;
    padding: 4px 14px;
  }
  .hub :deep(.linea) {
    width: 96px;
  }
}
</style>
