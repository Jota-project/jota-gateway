<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getSession, listSessions } from '@/api/admin'
import { ApiError } from '@/api/http'
import type { SessionDetail, SessionList, SessionStatus } from '@/api/types'
import FgBoton from '@/components/FgBoton.vue'
import { formatDuration, formatModes, formatSeconds } from '@/lib/format'
import { label, SESSION_STATUS, STAGE } from '@/lib/labels'
import { turnos } from '@/lib/turns'
import { useClientsStore } from '@/stores/clients'
import { useLiveStore } from '@/stores/live'

const route = useRoute()
const router = useRouter()
const live = useLiveStore()
const clients = useClientsStore()

const lista = shallowRef<SessionList | null>(null)
const error = ref<string | null>(null)
async function cargarLista(): Promise<void> {
  try {
    lista.value = await listSessions()
    error.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'No llego al gateway'
  }
}
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  void cargarLista()
  timer = setInterval(() => document.visibilityState === 'visible' && void cargarLista(), 5000)
})
onBeforeUnmount(() => clearInterval(timer))

type Filtro = 'todas' | SessionStatus
const FILTROS: { id: Filtro; texto: string }[] = [
  { id: 'todas', texto: 'Todas' },
  { id: 'active', texto: 'Abiertas' },
  { id: 'completed', texto: 'Cerradas' },
  { id: 'error', texto: 'Con error' },
  { id: 'revoked', texto: 'Acceso retirado' },
]
const filtro = ref<Filtro | null>(null)
const cuenta = (f: Filtro) => (lista.value?.sessions ?? []).filter((s) => f === 'todas' || s.status === f).length
const filtroActivo = computed<Filtro>(() => filtro.value ?? (cuenta('active') ? 'active' : 'todas'))

// El nombre sale del cliente (BD) o, si ya no existe, de la sesión viva.
const nombres = computed(() => {
  const m = new Map<string, string>()
  for (const s of live.state?.sessions ?? []) m.set(s.client_id, s.client_name)
  for (const c of clients.items) m.set(c.id, c.name)
  return m
})
const nombre = (clientId: string) => nombres.value.get(clientId) ?? 'Cliente borrado'

const filas = computed(() =>
  [...(lista.value?.sessions ?? [])]
    .filter((s) => filtroActivo.value === 'todas' || s.status === filtroActivo.value)
    .sort((a, b) => b.started_at.localeCompare(a.started_at))
    .map((s) => ({
      id: s.session_id,
      nombre: nombre(s.client_id),
      modo: formatModes(s.input_mode, s.output_mode),
      estado: label(SESSION_STATUS, s.status),
      viva: s.status === 'active',
      empezo: new Date(s.started_at).toLocaleString('es-ES', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }),
      turnos: s.turn_count,
      ultimo: formatSeconds(s.last_latencies.turn_e2e_ms),
    })),
)

const abiertas = computed(() => cuenta('active'))
const conError = computed(() => cuenta('error'))
const resumen = computed(() => {
  if (!lista.value) return ''
  const partes = [abiertas.value ? `${abiertas.value} ${abiertas.value === 1 ? 'abierta' : 'abiertas'} ahora` : 'Ninguna abierta ahora']
  if (conError.value) partes.push(`${conError.value} con error en las ${lista.value.total} últimas`)
  return `${partes.join('; ')}.`
})

// Detalle: la sesión de la ruta, con sus turnos.
const seleccion = computed(() => (typeof route.params.id === 'string' ? route.params.id : null))
const detalle = shallowRef<SessionDetail | null>(null)
const detalleError = ref<string | null>(null)
const turnoSel = ref<number | null>(null)
async function cargarDetalle(id: string): Promise<void> {
  try {
    detalle.value = await getSession(id)
    detalleError.value = null
  } catch (e) {
    detalle.value = null
    detalleError.value = e instanceof ApiError && e.kind === 'not-found' ? 'Esa sesión ya no está en memoria.' : 'No llego al gateway.'
  }
}
watch(
  seleccion,
  (id) => {
    turnoSel.value = null
    detalle.value = null
    if (id) void cargarDetalle(id)
  },
  { immediate: true },
)
watch(lista, () => seleccion.value && void cargarDetalle(seleccion.value))

const listaTurnos = computed(() => (detalle.value ? turnos(detalle.value.events) : []))
const turno = computed(() => {
  const l = listaTurnos.value
  if (!l.length) return null
  return l.find((t) => t.numero === turnoSel.value) ?? l[l.length - 1]!
})
const indice = computed(() => (turno.value ? listaTurnos.value.indexOf(turno.value) : -1))
function mover(paso: number) {
  const t = listaTurnos.value[indice.value + paso]
  if (t) turnoSel.value = t.numero
}
const CLAVE = new Set(['llm_first_token', 'tts_first_chunk'])
const barras = computed(() => {
  const t = turno.value
  if (!t) return []
  const total = Math.max(t.total, 1)
  return t.etapas.map((e) => ({
    texto: label(STAGE, e.stage),
    clave: CLAVE.has(e.stage),
    izquierda: `${(e.desde / total) * 100}%`,
    ancho: `${Math.max(((e.hasta - e.desde) / total) * 100, 0.8)}%`,
    cifra: formatSeconds(e.hasta),
  }))
})
const media = computed(() => {
  const s = detalle.value?.summary
  if (!s) return ''
  const partes = []
  if (s.avg_turn_e2e_ms != null) partes.push(`${formatSeconds(s.avg_turn_e2e_ms)} por turno`)
  if (s.avg_llm_first_token_ms != null) partes.push(`${formatSeconds(s.avg_llm_first_token_ms)} al primer token`)
  if (s.duration_s != null) partes.push(`duró ${formatDuration(s.duration_s)}`)
  return partes.length ? `Media de la sesión: ${partes.join(', ')}.` : ''
})
</script>

<template>
  <div class="sesiones">
    <header class="titulo">
      <h1 class="fg-h1">Sesiones</h1>
      <p v-if="resumen" class="fg-sub">{{ resumen }}</p>
      <p class="fg-meta">Las 100 últimas, en memoria: se borran al reiniciar el gateway.</p>
    </header>

    <div class="cuerpo">
      <section aria-label="Lista de sesiones" class="lista">
        <div class="fg-pestanas" role="tablist" aria-label="Filtrar por estado">
          <button
            v-for="f in FILTROS"
            :key="f.id"
            type="button"
            role="tab"
            :aria-selected="filtroActivo === f.id"
            @click="filtro = f.id"
          >
            {{ f.texto }}<small>{{ cuenta(f.id) }}</small>
          </button>
        </div>
        <p v-if="error" class="fg-meta">No pude leer las sesiones: {{ error }}</p>
        <div v-else-if="filas.length" class="fg-scroll">
          <table class="fg-tabla">
            <thead>
              <tr>
                <th>Aparato</th>
                <th class="opcional">Modo</th>
                <th>Empezó</th>
                <th class="num">Turnos</th>
                <th class="num">Último turno</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in filas" :key="f.id" :class="{ viva: f.viva, elegida: f.id === seleccion }">
                <td>
                  <RouterLink :to="`/sesiones/${encodeURIComponent(f.id)}`" :aria-current="f.id === seleccion ? 'true' : undefined" class="nombre">{{ f.nombre }}</RouterLink>
                  <span v-if="filtroActivo === 'todas'" class="fg-meta"> · {{ f.estado }}</span>
                </td>
                <td class="fg-tenue opcional">{{ f.modo }}</td>
                <td class="fg-tenue">{{ f.empezo }}</td>
                <td class="num">{{ f.turnos }}</td>
                <td class="num">{{ f.ultimo }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else-if="lista" class="fg-meta">No hay sesiones aquí.</p>
      </section>

      <section v-if="seleccion" aria-labelledby="h-turno" class="detalle">
        <template v-if="detalle && turno">
          <div class="cab">
            <h2 id="h-turno" class="fg-h2">{{ nombre(detalle.client_id) }} · turno {{ indice + 1 }} <small>de {{ listaTurnos.length }}</small></h2>
            <div class="pasos">
              <FgBoton tam="sm" solo-icono icono="izquierda" aria-label="Turno anterior" :disabled="indice <= 0" @click="mover(-1)" />
              <FgBoton tam="sm" solo-icono icono="derecha" aria-label="Turno siguiente" :disabled="indice >= listaTurnos.length - 1" @click="mover(1)" />
            </div>
          </div>
          <ol class="etapas" :aria-label="`Etapas del turno ${indice + 1}`">
            <li v-for="(b, i) in barras" :key="i" :class="{ clave: b.clave }">
              <span class="etiqueta">{{ b.texto }}</span>
              <span class="pista" aria-hidden="true"><span class="barra" :style="{ marginLeft: b.izquierda, width: b.ancho }" /></span>
              <span class="cifra">{{ b.cifra }}</span>
            </li>
          </ol>
          <p class="fg-meta">Cada barra va desde que acaba la etapa anterior; la cifra es el tiempo desde el principio del turno.</p>
          <p v-if="media" class="fg-meta">{{ media }}</p>
        </template>
        <template v-else-if="detalle">
          <h2 id="h-turno" class="fg-h2">{{ nombre(detalle.client_id) }}</h2>
          <p class="fg-meta">Esta sesión aún no tiene turnos.</p>
        </template>
        <p v-else-if="detalleError" class="fg-meta">{{ detalleError }}</p>
        <p v-else class="fg-meta">Cargando…</p>
        <FgBoton tam="sm" class="cerrar" @click="router.push('/sesiones')">Cerrar</FgBoton>
      </section>
    </div>
  </div>
</template>

<style scoped>
.sesiones {
  display: flex;
  flex-direction: column;
  gap: 32px;
}
.titulo {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cuerpo {
  display: flex;
  flex-wrap: wrap;
  gap: 32px;
  align-items: flex-start;
}
.lista {
  flex: 3 1 520px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.nombre {
  font-weight: 600;
}
tr.elegida td {
  background: var(--piedra-alta);
}
.detalle {
  flex: 2 1 380px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 22px;
  border-radius: var(--radio-bloque);
  background: var(--piedra);
}
.cab {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.cab h2 {
  flex: 1 1 auto;
}
.pasos {
  display: flex;
  gap: 8px;
}
.etapas {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.etapas li {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr) 56px;
  gap: 12px;
  align-items: center;
  font-size: 14px;
}
.etiqueta {
  color: var(--hueso-suave);
}
.pista {
  height: 8px;
  border-radius: 4px;
  background: var(--veta);
}
.barra {
  display: block;
  height: 8px;
  border-radius: 4px;
  background: var(--hueso-suave);
}
.clave .etiqueta,
.clave .cifra {
  color: var(--hueso);
  font-weight: 550;
}
.clave .barra {
  background: var(--hueso);
}
.cifra {
  text-align: right;
}
.cerrar {
  align-self: flex-start;
}
@media (max-width: 760px) {
  .etapas li {
    grid-template-columns: 120px minmax(0, 1fr) 48px;
  }
}
</style>
