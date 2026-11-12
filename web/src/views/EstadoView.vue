<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { getOrchestratorStatus, getReady, getTranscriberStatus, getTtsStatus, reconnectOrchestrator } from '@/api/admin'
import type { ServiceDetail } from '@/api/types'
import FgBoton from '@/components/FgBoton.vue'
import FgEstado from '@/components/FgEstado.vue'
import LienzoServicios from '@/components/LienzoServicios.vue'
import { formatDuration, formatModes, formatSeconds } from '@/lib/format'
import { finding, headline, serviceTone } from '@/lib/health'
import { CLOSE_REASON, HANDSHAKE_REJECTED, label, PROVIDER, TURN_ERROR } from '@/lib/labels'
import { toast } from '@/lib/toasts'
import { useLiveStore } from '@/stores/live'

// ¿Fogar responde ahora? El titular lo contesta; el lienzo enseña quién habla con quién.
const live = useLiveStore()
type Key = 'orchestrator' | 'stt' | 'tts'
const KEYS: Key[] = ['orchestrator', 'stt', 'tts']

// Detalle de cada servicio (reintentos, conectado desde). El estado lo manda el flujo vivo.
const detalles = shallowRef<Partial<Record<Key, ServiceDetail>>>({})
const FETCH: Record<Key, () => Promise<ServiceDetail>> = {
  orchestrator: getOrchestratorStatus,
  stt: getTranscriberStatus,
  tts: getTtsStatus,
}
async function cargar(key: Key): Promise<void> {
  try {
    const d = await FETCH[key]()
    detalles.value = { ...detalles.value, [key]: d }
  } catch {
    // sin detalle se ve lo del flujo vivo
  }
}
// El transcriptor sondea de verdad en cada consulta: solo al entrar y al comprobar.
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  KEYS.forEach((k) => void cargar(k))
  timer = setInterval(() => {
    if (document.visibilityState !== 'visible') return
    void cargar('orchestrator')
    void cargar('tts')
  }, 30_000)
})
onBeforeUnmount(() => clearInterval(timer))
for (const key of KEYS) {
  watch(
    () => live.state?.services[key].state,
    (now, before) => {
      if (before && now !== before) void cargar(key)
    },
  )
}

const intentos = computed(() =>
  Object.fromEntries(KEYS.map((k) => [k, detalles.value[k]?.reconnect_attempts ?? 0])) as Record<Key, number>,
)
const titulo = computed(() => headline(live.state, live.connection))
const hallazgo = computed(() => finding(live.state, live.connection, intentos.value))
const frescura = computed(() => {
  if (live.connection === 'offline') return 'Sin conexión con el gateway'
  const g = live.state?.gateway
  if (!g) return 'Conectando…'
  return `En vivo · encendido hace ${formatDuration(g.uptime_s)}`
})
const caidoOpenclaw = computed(() => !!live.state && serviceTone(live.state.services.orchestrator) !== 'ok')

const comprobando = ref(false)
async function comprobar(): Promise<void> {
  if (comprobando.value) return
  comprobando.value = true
  try {
    const r = await getReady()
    void cargar('stt')
    const malos = Object.entries(r.services).filter(([, v]) => v !== 'ok').length
    toast(malos ? 'warn' : 'ok', malos ? `${malos} ${malos === 1 ? 'servicio no responde' : 'servicios no responden'}` : 'Los tres servicios responden')
  } catch {
    toast('bad', 'No se pudieron comprobar los servicios', 'El gateway no respondió a tiempo. Prueba otra vez.')
  } finally {
    comprobando.value = false
  }
}
const reconectando = ref(false)
async function reconectar(): Promise<void> {
  if (reconectando.value) return
  reconectando.value = true
  try {
    await reconnectOrchestrator()
    toast('ok', 'Reconectando OpenClaw', 'Su estado cambia aquí en cuanto vuelva.')
  } catch {
    toast('bad', 'No se pudo pedir la reconexión', 'Prueba otra vez en unos segundos.')
  } finally {
    reconectando.value = false
  }
}

// El servicio peor, para «Qué le pasa a…»: solo si algo falla de verdad (`off` no es un fallo).
const peor = computed(() => {
  const s = live.state?.services
  if (!s) return null
  const key = KEYS.find((k) => ['bad', 'warn'].includes(serviceTone(s[k])))
  if (!key) return null
  const snap = s[key]
  const tono = serviceTone(snap)
  const proveedor = 'provider' in snap ? label(PROVIDER, snap.provider) : 'OpenClaw'
  const nombre = { orchestrator: 'OpenClaw', stt: 'la transcripción', tts: 'la voz' }[key]
  const efecto = {
    orchestrator: 'Nadie recibe respuesta hasta que vuelva.',
    stt: 'Los aparatos no pueden hablarle por voz; por texto, sí.',
    tts: 'Las respuestas salen en texto hasta que vuelva.',
  }[key]
  return {
    key,
    nombre,
    tono: tono === 'warn' ? ('warn' as const) : ('bad' as const),
    resumen: `${tono === 'warn' ? 'Reconectando con' : 'No responde'} ${proveedor}`,
    efecto,
    error: snap.last_error,
    intentos: intentos.value[key],
  }
})

const conectados = computed(() =>
  (live.state?.sessions ?? []).map((row) => ({
    id: row.session_id,
    nombre: row.client_name,
    modo: formatModes(row.input_mode, row.output_mode),
    ahora:
      row.turn.kind === 'idle'
        ? 'En espera'
        : `${row.turn.kind === 'push' ? 'Fogar habla' : 'Turno abierto'}${row.turn.since_s != null ? ` · ${formatDuration(row.turn.since_s)}` : ''}`,
    viva: row.turn.kind !== 'idle',
    turnos: row.turn_count,
    ultimo: formatSeconds(row.last_latencies?.turn_e2e_ms),
    edad: formatDuration(row.age_s),
  })),
)

function principal(values: Record<string, number>, texts: Record<string, string>): string | null {
  const top = Object.entries(values)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])[0]
  return top ? label(texts, top[0]).toLowerCase() : null
}
const totales = computed(() => {
  const t = live.state?.totals
  if (!t) return null
  const suma = (v: Record<string, number>) => Object.values(v).reduce((a, b) => a + b, 0)
  const cerradas = suma(t.sessions_closed)
  const rechazadas = suma(t.handshake_rejected)
  const errores = suma(t.turn_errors)
  const num = (n: number) => n.toLocaleString('es-ES')
  return [
    { cifra: num(t.sessions_opened), texto: `sesiones, ${num(cerradas)} cerradas${principal(t.sessions_closed, CLOSE_REASON) ? ` (sobre todo: ${principal(t.sessions_closed, CLOSE_REASON)})` : ''}` },
    { cifra: num(t.turns.normal + t.turns.push), texto: `turnos, ${num(t.turns.push)} de Fogar sin preguntar` },
    { cifra: num(rechazadas), texto: rechazadas ? `conexiones rechazadas: ${principal(t.handshake_rejected, HANDSHAKE_REJECTED)}` : 'conexiones rechazadas' },
    { cifra: num(errores), texto: errores ? `turnos con error: ${principal(t.turn_errors, TURN_ERROR)}` : 'turnos con error' },
  ]
})
</script>

<template>
  <div class="pantalla">
    <header class="titulo">
      <div class="texto">
        <h1 class="fg-h1" aria-live="polite">{{ titulo }}</h1>
        <p v-if="hallazgo" class="fg-sub">{{ hallazgo }}</p>
        <p class="fg-meta">{{ frescura }}</p>
      </div>
      <FgBoton v-if="caidoOpenclaw" variante="principal" :ocupado="reconectando" @click="reconectar">Reconectar OpenClaw</FgBoton>
      <FgBoton :ocupado="comprobando" @click="comprobar">Comprobar servicios</FgBoton>
    </header>

    <section aria-labelledby="h-casa" class="bloque">
      <h2 id="h-casa" class="fg-h2">Quién habla con quién</h2>
      <LienzoServicios v-if="live.state" :state="live.state" :detalles="detalles" />
      <p v-else class="fg-meta">Esperando al gateway…</p>
      <p class="fg-meta nota">El hilo encendido es un turno que está pasando ahora. La línea punteada es un servicio que no responde; se reintenta solo.</p>
    </section>

    <div class="fila">
      <section aria-labelledby="h-ahora" class="bloque ancho">
        <h2 id="h-ahora" class="fg-h2">Conectados ahora <small>· {{ conectados.length }}</small></h2>
        <div v-if="conectados.length" class="fg-scroll">
          <table class="fg-tabla tabla-ahora">
            <thead>
              <tr>
                <th>Aparato</th>
                <th class="opcional">Modo</th>
                <th>Ahora</th>
                <th class="num">Turnos</th>
                <th class="num">Último turno</th>
                <th class="num opcional">Conectado</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in conectados" :key="c.id" :class="{ viva: c.viva }">
                <td><RouterLink :to="`/sesiones/${encodeURIComponent(c.id)}`">{{ c.nombre }}</RouterLink></td>
                <td class="fg-tenue opcional">{{ c.modo }}</td>
                <td :class="{ 'fg-tenue': !c.viva }">{{ c.ahora }}</td>
                <td class="num">{{ c.turnos }}</td>
                <td class="num">{{ c.ultimo }}</td>
                <td class="num fg-tenue opcional">{{ c.edad }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="fg-meta">Nadie conectado. Aquí aparecen la tele, los altavoces y el resto de aparatos mientras tengan una sesión abierta.</p>
      </section>

      <section v-if="peor" aria-labelledby="h-peor" class="bloque estrecho">
        <h2 id="h-peor" class="fg-h2">Qué le pasa a {{ peor.nombre }}</h2>
        <div class="peor">
          <FgEstado :tono="peor.tono" :texto="peor.resumen" class="peor-titulo" />
          <p class="fg-suave">{{ peor.efecto }} El gateway reintenta solo, cada vez más espaciado<template v-if="peor.intentos"> (lleva {{ peor.intentos }})</template>.</p>
          <p v-if="peor.error" class="fg-meta">Último error: <code>{{ peor.error }}</code></p>
          <p class="fg-meta">Si no vuelve en unos minutos, revisa su configuración con <code>fogar-gw doctor</code>.</p>
        </div>
      </section>
    </div>

    <section v-if="totales" aria-labelledby="h-desde" class="bloque">
      <h2 id="h-desde" class="fg-h2">Desde que se encendió</h2>
      <div class="franja">
        <p v-for="(t, i) in totales" :key="i"><span class="cifra">{{ t.cifra }}</span>{{ t.texto }}</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.pantalla {
  display: flex;
  flex-direction: column;
  gap: 40px;
}
.titulo {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 12px 16px;
}
.titulo .texto {
  flex: 1 1 420px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.bloque {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
.nota {
  max-width: 70ch;
}
.fila {
  display: flex;
  flex-wrap: wrap;
  gap: 40px;
}
.ancho {
  flex: 2 1 520px;
}
.estrecho {
  flex: 1 1 280px;
}
.tabla-ahora {
  min-width: 340px;
}
.peor {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 18px;
  border-radius: var(--radio-bloque);
  background: var(--piedra);
}
.peor p {
  margin: 0;
}
.peor-titulo {
  font-weight: 600;
}
.peor code {
  color: var(--hueso);
}
.franja {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border-radius: var(--radio-bloque);
  background: var(--piedra);
}
.franja p {
  margin: 0;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 14px;
  color: var(--hueso-tenue);
}
.franja p + p {
  border-left: 1px solid var(--veta);
}
.cifra {
  font-size: 32px;
  font-weight: 500;
  line-height: 1.1;
  letter-spacing: -0.01em;
  color: var(--hueso);
}
@media (max-width: 760px) {
  .pantalla {
    gap: 28px;
  }
  .titulo :deep(.boton) {
    flex: 1 1 100%;
  }
  .titulo .texto {
    flex-basis: 100%;
  }
  .franja {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .franja p:nth-child(3) {
    border-left: 0;
  }
  .franja p:nth-child(n + 3) {
    border-top: 1px solid var(--veta);
  }
}
</style>
