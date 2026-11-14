<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { createConversation, getConversationAgents } from '@/api/admin'
import { ApiError } from '@/api/http'
import type { ConversationAgents } from '@/api/types'
import FgBoton from '@/components/FgBoton.vue'
import { conversationKind, conversationTitle } from '@/lib/conversations'
import { formatRelative } from '@/lib/format'
import { useConversationsStore } from '@/stores/conversations'
import { useLiveStore } from '@/stores/live'

// Todo lo que se ha hablado con Fogar en casa, de cada aparato. Lo dicho y el título viven en OpenClaw.
const router = useRouter()
const conversations = useConversationsStore()
const live = useLiveStore()

let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  void conversations.refresh()
  timer = setInterval(() => document.visibilityState === 'visible' && void conversations.refresh(), 10_000)
})
onBeforeUnmount(() => clearInterval(timer))

// Filtro por aparato: los que tienen alguna conversación, por nombre.
const aparato = ref<string>('todos')
const aparatos = computed(() => {
  const m = new Map<string, { id: string; nombre: string; cuenta: number }>()
  for (const c of conversations.items) {
    const a = m.get(c.client_id) ?? { id: c.client_id, nombre: c.client_name, cuenta: 0 }
    a.cuenta += 1
    m.set(c.client_id, a)
  }
  return [...m.values()].sort((a, b) => a.nombre.localeCompare(b.nombre, 'es'))
})
const filtro = computed(() => (aparatos.value.some((a) => a.id === aparato.value) ? aparato.value : 'todos'))

// Abierta ahora: alguna sesión viva está en ella.
const abiertas = computed(() => new Set((live.state?.sessions ?? []).flatMap((s) => (s.conversation ? [s.conversation.id] : []))))

const ahora = ref(new Date())
const reloj = setInterval(() => (ahora.value = new Date()), 30_000)
onBeforeUnmount(() => clearInterval(reloj))

const filas = computed(() =>
  conversations.items
    .filter((c) => filtro.value === 'todos' || c.client_id === filtro.value)
    .map((c) => ({
      id: c.id,
      titulo: conversationTitle(c, ahora.value),
      sinTitulo: !c.title?.trim(),
      clase: conversationKind(c.kind),
      aparato: c.client_name,
      agente: c.agent,
      cuando: c.last_turn_at ? formatRelative(c.last_turn_at, ahora.value) : 'Sin mensajes todavía',
      turnos: c.turn_count,
      abierta: abiertas.value.has(c.id),
    })),
)

const titulo = computed(() => {
  const n = conversations.items.length
  if (!conversations.loaded) return 'Conversaciones'
  if (!n) return 'Todavía no se ha hablado con Fogar'
  return `${n} ${n === 1 ? 'conversación' : 'conversaciones'} en casa`
})

// Nueva conversación del panel: con el agente que se elija (por defecto, el que toque).
const nueva = ref(false)
const agentes = shallowRef<ConversationAgents | null>(null)
const agente = ref('')
const creando = ref(false)
const nuevaError = ref<string | null>(null)

async function abrirNueva(): Promise<void> {
  nueva.value = true
  nuevaError.value = null
  try {
    agentes.value = await getConversationAgents()
    agente.value = agentes.value.default ?? agentes.value.agents[0] ?? ''
  } catch (e) {
    agentes.value = null
    nuevaError.value = textoError(e)
  }
}
const opciones = computed(() => {
  const a = agentes.value
  if (!a) return []
  const lista = a.default && !a.agents.includes(a.default) ? [a.default, ...a.agents] : a.agents
  return lista.map((id) => ({ id, texto: id === a.default ? `${id} (por defecto)` : id }))
})

function textoError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.message === 'panel_client_inactive') return 'El cliente «Panel web» está desactivado. Actívalo en Clientes para escribir desde aquí.'
    if (e.message === 'agent_not_permitted') return 'Ese agente no está permitido para el panel (agentes permitidos de «Panel web»).'
    if (e.message === 'agent_not_available') return 'OpenClaw no tiene ese agente ahora.'
  }
  return 'No he podido hablar con el gateway. Prueba otra vez.'
}

async function empezar(): Promise<void> {
  if (creando.value) return
  creando.value = true
  nuevaError.value = null
  try {
    const elegido = agente.value && agente.value !== agentes.value?.default ? agente.value : null
    const c = await createConversation(elegido)
    conversations.add(c)
    nueva.value = false
    void router.push(`/conversaciones/${encodeURIComponent(c.id)}`)
  } catch (e) {
    nuevaError.value = textoError(e)
  } finally {
    creando.value = false
  }
}
</script>

<template>
  <div class="conversaciones">
    <header class="titulo">
      <div class="texto">
        <h1 class="fg-h1">{{ titulo }}</h1>
        <p class="fg-sub">Lo que cada aparato ha hablado con Fogar. Desde aquí se leen todas y se escribe en las del panel.</p>
      </div>
      <FgBoton variante="principal" icono="mas" :disabled="nueva" @click="abrirNueva">Nueva conversación</FgBoton>
    </header>

    <div class="cuerpo">
      <section aria-label="Lista de conversaciones" class="lista">
        <div v-if="aparatos.length > 1" class="fg-pestanas" role="tablist" aria-label="Filtrar por aparato">
          <button type="button" role="tab" :aria-selected="filtro === 'todos'" @click="aparato = 'todos'">
            Todas<small>{{ conversations.items.length }}</small>
          </button>
          <button v-for="a in aparatos" :key="a.id" type="button" role="tab" :aria-selected="filtro === a.id" @click="aparato = a.id">
            {{ a.nombre }}<small>{{ a.cuenta }}</small>
          </button>
        </div>
        <p v-if="conversations.error && !conversations.items.length" class="fg-meta">No pude leer las conversaciones: {{ conversations.error }}</p>
        <div v-else-if="filas.length" class="fg-scroll">
          <table class="fg-tabla">
            <thead>
              <tr>
                <th>Conversación</th>
                <th>Aparato</th>
                <th class="opcional">Agente</th>
                <th>Última vez</th>
                <th class="num opcional">Turnos</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in filas" :key="f.id" :class="{ viva: f.abierta }">
                <td>
                  <RouterLink :to="`/conversaciones/${encodeURIComponent(f.id)}`" class="nombre" :class="{ 'sin-titulo': f.sinTitulo }">{{ f.titulo }}</RouterLink>
                  <span v-if="f.abierta" class="fg-meta junto"> · abierta ahora</span>
                  <span v-else-if="f.clase" class="fg-meta junto"> · {{ f.clase }}</span>
                </td>
                <td class="fg-suave">{{ f.aparato }}</td>
                <td class="fg-tenue opcional">{{ f.agente }}</td>
                <td class="fg-tenue junto">{{ f.cuando }}</td>
                <td class="num opcional">{{ f.turnos }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else-if="conversations.loaded" class="fg-meta">
          Aquí aparece cada conversación en cuanto alguien hable con Fogar: la tele, los altavoces, Home Assistant o tú desde el panel.
        </p>
        <p class="fg-meta">Sin título: OpenClaw todavía no le ha puesto uno, o ahora no responde.</p>
      </section>

      <aside v-if="nueva" class="lado">
        <form class="nueva" novalidate @submit.prevent="empezar">
          <h2 class="fg-h2">Nueva conversación</h2>
          <p class="fg-suave">Escribes como «Panel web». La conversación se queda con el agente que elijas.</p>
          <div v-if="opciones.length" class="campo">
            <label for="nueva-agente">Agente</label>
            <select id="nueva-agente" v-model="agente">
              <option v-for="o in opciones" :key="o.id" :value="o.id">{{ o.texto }}</option>
            </select>
          </div>
          <p v-else-if="agentes" class="error" role="alert">
            Ahora no hay ningún agente con el que empezar: OpenClaw no ha dado su lista y al panel no le toca ninguno por defecto. Revisa los
            agentes permitidos de «Panel web» en Clientes.
          </p>
          <p v-else-if="!nuevaError" class="fg-meta">Cargando agentes…</p>
          <p v-if="nuevaError" class="error" role="alert">{{ nuevaError }}</p>
          <div class="acciones">
            <FgBoton type="submit" variante="principal" :ocupado="creando" :disabled="!opciones.length">Empezar</FgBoton>
            <FgBoton @click="nueva = false">Cancelar</FgBoton>
          </div>
        </form>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.conversaciones {
  display: flex;
  flex-direction: column;
  gap: 32px;
}
.titulo {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
}
.titulo .texto {
  flex: 1 1 420px;
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
  flex: 3 1 540px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.nombre {
  font-weight: 600;
}
.junto {
  white-space: nowrap;
}
.sin-titulo {
  font-weight: 500;
  color: var(--hueso-suave);
}
.lado {
  flex: 2 1 340px;
  min-width: 0;
}
.nueva {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 22px;
  border-radius: var(--radio-bloque);
  background: var(--piedra);
}
.nueva p {
  margin: 0;
}
.campo {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.campo label {
  font-size: 14px;
  font-weight: 550;
}
.campo select {
  height: 44px;
  padding: 0 12px;
  border-radius: var(--radio-nodo);
  border: 1px solid var(--veta);
  background: var(--noche);
  color: var(--hueso);
}
.error {
  font-size: 14px;
}
.error::before {
  content: '';
  display: inline-block;
  width: 12px;
  height: 11px;
  margin-right: 7px;
  vertical-align: -1px;
  background: var(--error);
  clip-path: polygon(50% 0, 100% 100%, 0 100%);
}
.acciones {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
@media (max-width: 760px) {
  .titulo :deep(.boton) {
    flex: 1 1 auto;
  }
  .lado {
    order: -1;
  }
}
</style>
