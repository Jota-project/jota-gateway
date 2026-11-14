<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, useTemplateRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getConversationMessages } from '@/api/admin'
import { ApiError } from '@/api/http'
import type { ConversationMessages } from '@/api/types'
import FgBoton from '@/components/FgBoton.vue'
import FgEstado from '@/components/FgEstado.vue'
import FgIcono from '@/components/FgIcono.vue'
import MensajeConversacion from '@/components/MensajeConversacion.vue'
import MensajeVivo from '@/components/MensajeVivo.vue'
import { createChatSession, type ChatSession } from '@/lib/chatSession'
import { canWrite, conversationTitle } from '@/lib/conversations'
import { formatClock, formatRelative } from '@/lib/format'
import { useConversationsStore } from '@/stores/conversations'
import { useSessionStore } from '@/stores/session'

// Una conversación: lo que se dijo (de OpenClaw) y, si es del panel, el chat en vivo para seguirla.
// Las de otros aparatos solo se leen (M19).
const route = useRoute()
const conversations = useConversationsStore()
const session = useSessionStore()

const id = computed(() => String(route.params.id ?? ''))
const conv = computed(() => conversations.byId.get(id.value) ?? null)
const escribible = computed(() => !!conv.value && canWrite(conv.value))
const texto = ref('')

// Lo dicho hasta ahora.
const historia = shallowRef<ConversationMessages | null>(null)
const historiaError = ref<string | null>(null)
const cargando = ref(false)
async function cargarHistoria(limit = 200): Promise<void> {
  const pedida = id.value // si cambias de conversación mientras llega, esta respuesta ya no vale
  cargando.value = true
  try {
    const leida = await getConversationMessages(pedida, limit)
    if (pedida !== id.value) return
    historia.value = leida
    historiaError.value = null
    // Lo terminado en vivo ya viene en lo leído: que no salga dos veces.
    chat.value?.historyReloaded()
  } catch (e) {
    if (pedida !== id.value) return
    historia.value = null
    if (e instanceof ApiError && e.status === 503) historiaError.value = 'OpenClaw no responde: ahora no puedo leer lo que se dijo.'
    else if (e instanceof ApiError && e.status === 502) historiaError.value = 'OpenClaw no ha querido dar lo que se dijo en esta conversación.'
    else if (e instanceof ApiError && e.kind === 'not-found') historiaError.value = 'Esta conversación ya no existe: se borró su aparato.'
    else historiaError.value = 'No llego al gateway.'
  } finally {
    if (pedida === id.value) cargando.value = false
  }
}

// El chat en vivo, solo en las del panel.
const chat = shallowRef<ChatSession | null>(null)
function cerrarChat(): void {
  chat.value?.dispose()
  chat.value = null
}
// Si la conversación estaba abierta en otra pestaña, aquella se queda con el aviso «Abierta en otro
// sitio» (el 1008 del gateway) y no vuelve sola: no hace falta mirarlo antes.
function abrirChat(): void {
  if (chat.value || !escribible.value || !session.token) return
  chat.value = createChatSession({ conversationId: id.value, token: session.token })
  chat.value.start()
}

watch(
  id,
  (nuevo) => {
    cerrarChat()
    historia.value = null
    historiaError.value = null
    texto.value = ''
    if (!nuevo) return
    void cargarHistoria()
    if (!conversations.byId.has(nuevo)) void conversations.refresh()
    abrirChat()
  },
  { immediate: true },
)
watch(escribible, (si) => si && abrirChat(), { immediate: true })
onBeforeUnmount(cerrarChat)

async function seguirAqui(): Promise<void> {
  chat.value?.retake()
  await cargarHistoria()
}

const ahora = ref(new Date())
const reloj = setInterval(() => (ahora.value = new Date()), 30_000)
onBeforeUnmount(() => clearInterval(reloj))

const titulo = computed(() => (conv.value ? conversationTitle(conv.value, ahora.value) : 'Conversación'))
const meta = computed(() => {
  const c = conv.value
  if (!c) return ''
  const cuando = c.last_turn_at ? `habló ${formatRelative(c.last_turn_at, ahora.value)}` : 'sin mensajes todavía'
  return `${c.client_name} · agente ${chat.value?.state.agent ?? c.agent} · ${cuando}`
})
const quienPersona = computed(() => (escribible.value ? 'Tú' : (conv.value?.client_name ?? 'Persona')))

const pasados = computed(() =>
  (historia.value?.messages ?? []).map((m, i) => ({
    key: `h-${i}`,
    props: {
      quien: m.role === 'user' ? quienPersona.value : 'Fogar',
      propio: m.role === 'user',
      texto: m.text,
      hora: m.timestamp ? formatClock(m.timestamp, ahora.value) : null,
    },
  })),
)
// Las líneas en vivo, tal cual: cada MensajeVivo calcula lo suyo.
const vivos = computed(() => chat.value?.state.log.lines ?? [])
const vacia = computed(() => historia.value && !pasados.value.length && !vivos.value.length)

// Estado del chat en una línea, y lo que se puede hacer.
const estado = computed(() => chat.value?.state ?? null)
const lineaEstado = computed<{ tono: 'ok' | 'warn' | 'off'; texto: string } | null>(() => {
  const s = estado.value
  if (!s) return null
  if (s.connection === 'ready')
    return s.orchestratorDown ? { tono: 'warn', texto: 'OpenClaw se está reconectando: las respuestas esperan a que vuelva' } : { tono: 'ok', texto: 'En vivo' }
  if (s.connection === 'connecting') return { tono: 'off', texto: 'Conectando…' }
  if (s.connection === 'reconnecting')
    return { tono: 'warn', texto: s.failures > 1 ? 'Sin conexión con el gateway. Reintento solo…' : 'Se cortó la conexión. Reconectando…' }
  if (s.connection === 'idle') return { tono: 'off', texto: 'En pausa por inactividad: se reabre al escribir' }
  return null
})
const turnoAbierto = computed(() => estado.value?.log.turnOpen ?? false)
// Al acabar un turno, la cabecera («habló hace…») y el título de OpenClaw se vuelven a leer.
watch(turnoAbierto, (abierto, antes) => antes && !abierto && void conversations.refresh())
const puedeEscribir = computed(() => estado.value?.connection === 'ready' || estado.value?.connection === 'idle')

const caja = useTemplateRef<HTMLTextAreaElement>('caja')
function enviar(): void {
  if (!chat.value || !texto.value.trim()) return
  if (chat.value.send(texto.value)) texto.value = ''
}
function alTeclear(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    enviar()
  }
}
// La caja crece con el texto hasta seis líneas.
watch(texto, () =>
  nextTick(() => {
    const el = caja.value
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`
  }),
)

// Si estás al final, lo nuevo se queda a la vista; si subiste a leer, no te mueve.
// Como mucho un desplazamiento por fotograma, aunque lleguen muchos tokens.
const firma = computed(() => `${pasados.value.length}:${vivos.value.length}:${vivos.value.at(-1)?.text.length ?? 0}`)
let alFinal = true
let fotograma = 0
const alDesplazar = () => (alFinal = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160)
window.addEventListener('scroll', alDesplazar, { passive: true })
onBeforeUnmount(() => {
  window.removeEventListener('scroll', alDesplazar)
  cancelAnimationFrame(fotograma)
})
watch(firma, () => {
  if (!alFinal || fotograma) return
  fotograma = requestAnimationFrame(() => {
    fotograma = 0
    window.scrollTo({ top: document.documentElement.scrollHeight })
  })
})
</script>

<template>
  <div class="conversacion">
    <header class="titulo">
      <RouterLink to="/conversaciones" class="volver fg-meta"><FgIcono nombre="izquierda" :tam="16" />Conversaciones</RouterLink>
      <h1 class="fg-h1">{{ titulo }}</h1>
      <p v-if="meta" class="fg-meta">{{ meta }}</p>
      <p v-else-if="conversations.loaded && !conv && !historia" class="fg-meta">No encuentro esta conversación.</p>
    </header>

    <p v-if="conv && !escribible" class="nota">
      Solo lectura: es de «{{ conv.client_name }}». Desde el panel se escribe en sus propias conversaciones; empieza una en
      <RouterLink to="/conversaciones">Conversaciones</RouterLink>.
    </p>

    <section class="hilo" aria-label="Lo que se ha dicho">
      <div v-if="historia?.has_more" class="anteriores">
        <p class="fg-meta">Hay mensajes anteriores.</p>
        <FgBoton tam="sm" :ocupado="cargando" @click="cargarHistoria(1000)">Ver hasta mil</FgBoton>
      </div>
      <MensajeConversacion v-for="m in pasados" :key="m.key" v-bind="m.props" />
      <div v-if="historiaError" class="fallo">
        <FgEstado tono="bad" :texto="historiaError" />
        <FgBoton tam="sm" :ocupado="cargando" @click="cargarHistoria()">Reintentar</FgBoton>
      </div>
      <p v-else-if="!historia && cargando" class="fg-meta">Leyendo lo que se dijo…</p>
      <p v-else-if="vacia" class="fg-meta">{{ escribible ? 'Todavía no habéis hablado. Escribe abajo para empezar.' : 'No hay nada dicho en esta conversación.' }}</p>
      <div v-if="vivos.length" class="vivos" aria-live="polite" aria-relevant="additions text">
        <MensajeVivo v-for="l in vivos" :key="l.key" :linea="l" :ahora="ahora" />
      </div>
    </section>

    <div v-if="estado" class="pie">
      <section v-if="estado.connection === 'elsewhere'" class="banner" role="status" aria-labelledby="h-fuera">
        <div>
          <h2 id="h-fuera" class="fg-h2">Abierta en otro sitio</h2>
          <p class="fg-suave">Esta conversación está abierta en otra pestaña o ventana del panel, y allí sigue. Si la sigues aquí, se cierra allí.</p>
        </div>
        <FgBoton variante="principal" @click="seguirAqui">Seguir aquí</FgBoton>
      </section>
      <section v-else-if="estado.connection === 'stopped'" class="banner" role="alert">
        <FgEstado tono="bad" :texto="estado.notice ?? 'El chat se ha parado.'" />
        <FgBoton @click="seguirAqui">Reintentar</FgBoton>
      </section>
      <form v-else class="redactar" @submit.prevent="enviar">
        <label for="mensaje" class="fg-solo-lector">Mensaje para Fogar</label>
        <textarea
          id="mensaje"
          ref="caja"
          v-model="texto"
          rows="1"
          placeholder="Escribe a Fogar"
          :disabled="!puedeEscribir"
          @keydown="alTeclear"
        />
        <FgBoton v-if="turnoAbierto" icono="parar" @click="chat?.cancel()">Parar</FgBoton>
        <FgBoton v-else type="submit" variante="principal" icono="enviar" :disabled="!puedeEscribir || !texto.trim()">Enviar</FgBoton>
      </form>
      <FgEstado v-if="lineaEstado" class="linea fg-meta" :tono="lineaEstado.tono" :texto="lineaEstado.texto" :tam="12" />
    </div>
  </div>
</template>

<style scoped>
.conversacion {
  display: flex;
  flex-direction: column;
  gap: 28px;
  max-width: 820px;
  min-height: calc(100vh - 100px);
}
.titulo {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.volver {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  align-self: flex-start;
}
.volver:hover {
  color: var(--hueso);
}
.nota {
  margin: 0;
  padding: 14px 16px;
  border-radius: var(--radio-bloque);
  background: var(--piedra);
  color: var(--hueso-suave);
  font-size: 14px;
}
.nota a {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.hilo,
.vivos {
  display: flex;
  flex-direction: column;
  gap: 22px;
}
.hilo {
  flex: 1 0 auto;
}
.anteriores,
.fallo {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.pie {
  position: sticky;
  bottom: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 0 20px;
  background: linear-gradient(to bottom, rgb(11 16 18 / 0), var(--noche) 18px);
}
.redactar {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 8px 8px 8px 16px;
  border-radius: 24px;
  background: var(--piedra);
  box-shadow: inset 0 0 0 1px var(--veta);
}
.redactar:focus-within {
  box-shadow: inset 0 0 0 1px var(--hueso-tenue);
}
textarea {
  flex: 1;
  min-width: 0;
  min-height: 44px;
  max-height: 168px;
  padding: 11px 0;
  border: 0;
  background: none;
  color: var(--hueso);
  font-size: 16px;
  line-height: 1.4;
  resize: none;
  outline: none;
}
textarea:disabled {
  opacity: 0.6;
}
.banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 16px;
  padding: 18px 20px;
  border-radius: var(--radio-bloque);
  background: var(--piedra-alta);
}
.banner > div {
  flex: 1 1 320px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.banner p {
  margin: 0;
}
.linea {
  padding-left: 16px;
}
@media (max-width: 760px) {
  .conversacion {
    min-height: calc(100vh - 180px);
  }
  .pie {
    bottom: calc(61px + env(safe-area-inset-bottom));
    padding-bottom: 12px;
  }
}
</style>
