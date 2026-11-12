<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import {
  getOrchestratorStatus,
  getReady,
  getTranscriberStatus,
  getTtsStatus,
  reconnectOrchestrator,
} from '@/api/admin'
import type { ReadyResponse, ServiceDetail } from '@/api/types'
import UiButton from '@/components/ui/UiButton.vue'
import UiOdometer from '@/components/ui/UiOdometer.vue'
import UiStatus from '@/components/ui/UiStatus.vue'
import { formatDateTime, formatDuration } from '@/lib/format'
import { headline, serviceTone } from '@/lib/health'
import {
  CLOSE_REASON,
  HANDSHAKE_REJECTED,
  label,
  MODE,
  PROVIDER,
  SERVICE_NAME,
  SERVICE_STATE,
  TURN_ERROR,
  TURN_KIND,
} from '@/lib/labels'
import { toast } from '@/lib/toasts'
import { useLiveStore } from '@/stores/live'

const live = useLiveStore()

type Key = 'orchestrator' | 'stt' | 'tts'
const READY_KEY = { orchestrator: 'orchestrator', stt: 'transcriber', tts: 'tts' } as const

const title = computed(() => headline(live.state, live.connection))
const words = computed(() => title.value.split(' '))

const meta = computed(() => {
  const g = live.state?.gateway
  if (!g) return ''
  const on = `ncendido el ${formatDateTime(g.started_at)}, hace ${formatDuration(g.uptime_s)}`
  return g.version ? `Versión ${g.version} · e${on}` : `E${on}`
})

// Detalle de cada servicio (conectado desde, reintentos). El estado manda el flujo vivo; esto lo completa.
const details = shallowRef<Partial<Record<Key, ServiceDetail>>>({})
const FETCH: Record<Key, () => Promise<ServiceDetail>> = {
  orchestrator: getOrchestratorStatus,
  stt: getTranscriberStatus,
  tts: getTtsStatus,
}

async function loadDetail(key: Key): Promise<void> {
  try {
    const detail = await FETCH[key]() // antes de copiar: las tres llegan a la vez
    details.value = { ...details.value, [key]: detail }
  } catch {
    // Sin detalle se ve lo del flujo vivo; un 401 ya lo gestiona el flujo.
  }
}

// El transcriptor hace un sondeo real en cada consulta: solo al entrar y al comprobar.
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  for (const key of ['orchestrator', 'stt', 'tts'] as const) void loadDetail(key)
  timer = setInterval(() => {
    if (document.visibilityState !== 'visible') return
    void loadDetail('orchestrator')
    void loadDetail('tts')
  }, 30_000)
})
onBeforeUnmount(() => clearInterval(timer))

// Cuando un servicio cambia de estado, su detalle (conectado desde, reintentos) también.
for (const key of ['orchestrator', 'stt', 'tts'] as const) {
  watch(
    () => live.state?.services[key].state,
    (now, before) => {
      if (before && now !== before) void loadDetail(key)
    },
  )
}

const ready = ref<ReadyResponse | null>(null)
const checking = ref(false)
async function check(): Promise<void> {
  if (checking.value) return
  checking.value = true
  try {
    ready.value = await getReady()
    void loadDetail('stt')
  } catch {
    toast('bad', 'No se pudieron comprobar los servicios', 'El gateway no respondió a tiempo. Prueba otra vez.')
  } finally {
    checking.value = false
  }
}

const reconnecting = ref(false)
async function reconnect(): Promise<void> {
  if (reconnecting.value) return
  reconnecting.value = true
  try {
    await reconnectOrchestrator()
    toast('ok', 'Reconectando OpenClaw', 'Su estado cambia aquí en cuanto vuelva.')
  } catch {
    toast('bad', 'No se pudo pedir la reconexión', 'Prueba otra vez en unos segundos.')
  } finally {
    reconnecting.value = false
  }
}

const services = computed(() => {
  const s = live.state?.services
  if (!s) return []
  return (['orchestrator', 'stt', 'tts'] as const).map((key) => {
    const snapshot = s[key]
    const tone = serviceTone(snapshot)
    const detail = details.value[key]
    const probe = ready.value?.services[READY_KEY[key]]
    return {
      key,
      name: SERVICE_NAME[key],
      provider: 'provider' in snapshot ? label(PROVIDER, snapshot.provider) : 'Orquestador',
      tone,
      state: tone === 'off' ? 'Desactivado' : SERVICE_STATE[snapshot.state],
      since: tone === 'ok' && detail?.connected_at ? formatDateTime(detail.connected_at) : null,
      attempts: tone !== 'off' && detail?.reconnect_attempts ? detail.reconnect_attempts : null,
      error: tone === 'off' ? null : snapshot.last_error,
      probe: tone === 'off' || !probe ? null : probe === 'ok' ? 'Responde' : 'No responde',
    }
  })
})

const sessions = computed(() =>
  (live.state?.sessions ?? []).map((row) => ({
    id: row.session_id,
    name: row.client_name,
    input: label(MODE, row.input_mode),
    output: row.output_mode.map((mode) => label(MODE, mode)).join(', '),
    turn:
      row.turn.kind === 'idle' || row.turn.since_s == null
        ? label(TURN_KIND, row.turn.kind)
        : `${label(TURN_KIND, row.turn.kind)} · ${formatDuration(row.turn.since_s)}`,
    busy: row.turn.kind !== 'idle',
    turns: row.turn_count,
    age: formatDuration(row.age_s),
    latency: row.last_latencies?.turn_e2e_ms != null ? `${Math.round(row.last_latencies.turn_e2e_ms)} ms` : '—',
  })),
)

function counts(values: Record<string, number>, texts: Record<string, string>) {
  return Object.entries(values)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([key, n]) => ({ key, text: label(texts, key), n }))
}

const totals = computed(() => {
  const t = live.state?.totals
  if (!t) return null
  return {
    opened: t.sessions_opened,
    closed: counts(t.sessions_closed, CLOSE_REASON),
    normal: t.turns.normal,
    push: t.turns.push,
    rejected: counts(t.handshake_rejected, HANDSHAKE_REJECTED),
    errors: counts(t.turn_errors, TURN_ERROR),
  }
})
</script>

<template>
  <div class="estado">
    <header>
      <h1 class="display" aria-live="polite">
        <Transition name="headline" mode="out-in">
          <span :key="title" class="words">
            <span v-for="(word, i) in words" :key="i" class="word" :style="{ animationDelay: `${i * 45}ms` }">{{
              word
            }}</span>
          </span>
        </Transition>
      </h1>
      <p v-if="meta" class="meta tab">{{ meta }}</p>
    </header>

    <section aria-labelledby="h-servicios">
      <div class="section-head">
        <h2 id="h-servicios">Servicios</h2>
        <div class="actions">
          <UiButton
            size="sm"
            variant="ghost"
            icon="estado"
            :busy="checking"
            :state-key="checking ? 1 : 0"
            :disabled="!live.state"
            @click="check"
          >
            {{ checking ? 'Comprobando…' : 'Comprobar servicios' }}
          </UiButton>
          <UiButton size="sm" :busy="reconnecting" :state-key="reconnecting ? 1 : 0" :disabled="!live.state" @click="reconnect">
            Reconectar OpenClaw
          </UiButton>
        </div>
      </div>

      <div v-if="services.length" class="services">
        <article v-for="service in services" :key="service.key" class="service" :class="service.tone">
          <h3>
            {{ service.name }} <span class="sub">{{ service.provider }}</span>
          </h3>
          <UiStatus :tone="service.tone" :label="service.state" />
          <dl class="tab">
            <template v-if="service.since">
              <dt>Conectado desde</dt>
              <dd>{{ service.since }}</dd>
            </template>
            <template v-if="service.attempts">
              <dt>Reintentos</dt>
              <dd>{{ service.attempts }}</dd>
            </template>
            <template v-if="service.error">
              <dt>Último error</dt>
              <dd>{{ service.error }}</dd>
            </template>
            <template v-if="service.probe">
              <dt>Comprobación</dt>
              <dd :class="{ fail: service.probe === 'No responde' }">{{ service.probe }}</dd>
            </template>
          </dl>
        </article>
      </div>
      <p v-else class="soft">Esperando el estado del gateway…</p>
    </section>

    <section aria-labelledby="h-conectados">
      <div class="section-head">
        <h2 id="h-conectados">Conectados ahora</h2>
        <span v-if="live.state" class="count tab"><UiOdometer :value="sessions.length" /></span>
      </div>
      <div v-if="sessions.length" class="table-wrap">
        <table class="tab">
          <thead>
            <tr>
              <th scope="col">Cliente</th>
              <th scope="col">Entrada</th>
              <th scope="col">Salida</th>
              <th scope="col">Ahora</th>
              <th scope="col" class="num">Turnos</th>
              <th scope="col" class="num">Abierta hace</th>
              <th scope="col" class="num">Última respuesta</th>
            </tr>
          </thead>
          <TransitionGroup tag="tbody" name="row">
            <tr v-for="row in sessions" :key="row.id">
              <th scope="row">{{ row.name }}</th>
              <td>{{ row.input }}</td>
              <td>{{ row.output }}</td>
              <td :class="{ busy: row.busy }">{{ row.turn }}</td>
              <td class="num">{{ row.turns }}</td>
              <td class="num">{{ row.age }}</td>
              <td class="num">{{ row.latency }}</td>
            </tr>
          </TransitionGroup>
        </table>
      </div>
      <p v-else-if="live.state" class="soft">
        Nadie conectado. Aquí aparecerán la tele, los altavoces y el resto de clientes mientras tengan una sesión abierta.
      </p>
    </section>

    <section v-if="totals" aria-labelledby="h-totales">
      <h2 id="h-totales">Desde que se encendió</h2>
      <div class="totals">
        <div class="group">
          <h3>Sesiones</h3>
          <p class="figure"><UiOdometer :value="totals.opened" /> <span>{{ totals.opened === 1 ? 'abierta' : 'abiertas' }}</span></p>
          <ul v-if="totals.closed.length">
            <li v-for="item in totals.closed" :key="item.key">
              <span>{{ item.text }}</span><span class="tab">{{ item.n }}</span>
            </li>
          </ul>
          <p v-else class="none">Ninguna cerrada</p>
        </div>
        <div class="group">
          <h3>Turnos</h3>
          <p class="figure"><UiOdometer :value="totals.normal" /> <span>{{ totals.normal === 1 ? 'respuesta' : 'respuestas' }}</span></p>
          <p class="figure"><UiOdometer :value="totals.push" /> <span>de Fogar sin preguntar</span></p>
        </div>
        <div class="group">
          <h3>Conexiones rechazadas</h3>
          <ul v-if="totals.rejected.length">
            <li v-for="item in totals.rejected" :key="item.key">
              <span>{{ item.text }}</span><span class="tab">{{ item.n }}</span>
            </li>
          </ul>
          <p v-else class="none">Ninguna</p>
        </div>
        <div class="group">
          <h3>Errores de turno</h3>
          <ul v-if="totals.errors.length">
            <li v-for="item in totals.errors" :key="item.key">
              <span>{{ item.text }}</span><span class="tab">{{ item.n }}</span>
            </li>
          </ul>
          <p v-else class="none">Ninguno</p>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.estado {
  display: flex;
  flex-direction: column;
  gap: 56px;
  max-width: 1080px;
}

header {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

h1 {
  margin: 0;
  line-height: 1.08;
  text-wrap: balance;
}

.words {
  display: inline;
}

.word {
  display: inline-block;
  margin-right: 0.24em;
  animation: j-blur-in var(--t-scene) var(--ease-out) both;
}

.headline-leave-active {
  transition: opacity var(--t-press) ease;
}

.headline-leave-to {
  opacity: 0;
}

.meta {
  margin: 0;
  font-size: 15.5px;
  color: var(--text-soft);
}

section {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.section-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px 16px;
}

h2 {
  margin: 0;
  font-family: var(--serif);
  font-size: 24px;
  font-weight: 400;
  font-variation-settings: 'opsz' 36; /* < 60 px: opsz 36 (base.css) */
  letter-spacing: -0.01em;
}

h3 {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.count {
  margin-right: auto;
  font-size: 15px;
  color: var(--text-faint);
}

.services {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 32px 40px;
}

.service {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sub {
  margin-left: 6px;
  font-size: 13px;
  font-weight: 400;
  color: var(--text-faint);
}

dl {
  margin: 0;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 16px;
  font-size: 14px;
}

dt {
  color: var(--text-faint);
}

dd {
  margin: 0;
  min-width: 0;
  color: var(--text-soft);
  overflow-wrap: anywhere;
}

dd.fail {
  color: var(--bad);
}

.soft {
  margin: 0;
  max-width: 62ch;
  color: var(--text-soft);
}

.table-wrap {
  overflow-x: auto;
  margin: 0 -12px;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  white-space: nowrap;
}

th,
td {
  padding: 11px 12px;
  text-align: left;
}

thead th {
  font-size: 12.5px;
  font-weight: 550;
  color: var(--text-faint);
}

tbody th {
  font-weight: 550;
}

tbody td {
  color: var(--text-soft);
}

tbody tr {
  box-shadow: inset 0 1px 0 var(--line);
  transition: background-color 1.2s var(--ease-out);
}

.num {
  text-align: right;
}

td.busy {
  color: var(--accent-text);
}

/* La fila que llega se ilumina y se apaga despacio; la que se va desaparece rápido. */
.row-enter-from {
  background-color: var(--accent-tint);
}

.row-leave-active {
  transition: opacity var(--t-quick) ease;
}

.row-leave-to {
  opacity: 0;
}

.totals {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 32px 40px;
}

.group {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.figure {
  margin: 0;
  display: flex;
  align-items: baseline;
  gap: 10px;
  color: var(--text-soft);
}

.figure :deep(.odo) {
  font-family: var(--serif);
  font-size: 24px;
  color: var(--text);
}

ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 14px;
  color: var(--text-soft);
}

li {
  display: flex;
  justify-content: space-between;
  gap: 16px;
}

.none {
  margin: 0;
  font-size: 14px;
  color: var(--text-faint);
}

</style>
