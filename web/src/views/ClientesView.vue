<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { createClient } from '@/api/admin'
import { ApiError } from '@/api/http'
import type { ClientType, ClientWithKey } from '@/api/types'
import ClaveNueva from '@/components/ClaveNueva.vue'
import FgBoton from '@/components/FgBoton.vue'
import FgCampo from '@/components/FgCampo.vue'
import FgEstado from '@/components/FgEstado.vue'
import FgIcono from '@/components/FgIcono.vue'
import FondoVivo from '@/components/FondoVivo.vue'
import PanelEmparejar from '@/components/PanelEmparejar.vue'
import { CLIENT_TYPE, label } from '@/lib/labels'
import { toast } from '@/lib/toasts'
import { useClientsStore } from '@/stores/clients'
import { useLiveStore } from '@/stores/live'

const router = useRouter()
const clients = useClientsStore()
const live = useLiveStore()
onMounted(() => void clients.refresh())

const pestana = ref<'activos' | 'desactivados'>('activos')
const busqueda = ref('')
const activos = computed(() => clients.items.filter((c) => c.is_active).length)
const desactivados = computed(() => clients.items.length - activos.value)
const conSesion = computed(() => new Set((live.state?.sessions ?? []).map((s) => s.client_id)))

const RESPONDE: Record<string, string> = { audio: 'voz', text: 'texto', status: 'avisos' }
const filas = computed(() => {
  const q = busqueda.value.trim().toLowerCase()
  return clients.items
    .filter((c) => c.is_active === (pestana.value === 'activos'))
    .filter((c) => !q || c.name.toLowerCase().includes(q) || c.key_fp.includes(q.replace(/\s/g, '')))
    .sort((a, b) => a.name.localeCompare(b.name, 'es'))
    .map((c) => ({
      id: c.id,
      nombre: c.name,
      tipo: c.client_type ? label(CLIENT_TYPE, c.client_type) : 'Sin tipo',
      responde: c.output_mode?.length ? c.output_mode.map((m) => RESPONDE[m] ?? m).join(' y ') : 'por defecto',
      conectado: conSesion.value.has(c.id),
      huella: `${c.key_fp.slice(0, 4)} ${c.key_fp.slice(4)}`,
    }))
})
const titulo = computed(() => {
  if (!clients.loaded) return 'Clientes'
  const n = activos.value
  if (!n) return 'Ningún aparato habla con Fogar'
  return `${n} ${n === 1 ? 'aparato habla' : 'aparatos hablan'} con Fogar`
})

// Panel de la derecha: emparejar (principal) o crear a mano; después de crear, la clave una sola vez.
const panel = ref<'emparejar' | 'crear' | null>(null)
const nuevoNombre = ref('')
const nuevoTipo = ref<ClientType | ''>('')
const nuevoError = ref<string | null>(null)
const creando = ref(false)
const creado = shallowRef<ClientWithKey | null>(null)

async function crear(): Promise<void> {
  const nombre = nuevoNombre.value.trim()
  if (!nombre) {
    nuevoError.value = 'Ponle un nombre.'
    return
  }
  creando.value = true
  nuevoError.value = null
  try {
    creado.value = await createClient({ name: nombre, client_type: nuevoTipo.value || null })
    void clients.refresh()
  } catch (e) {
    nuevoError.value = e instanceof ApiError && e.kind === 'invalid' ? 'Ese nombre no vale: entre 1 y 200 caracteres.' : 'No he podido crearlo. Prueba otra vez.'
  } finally {
    creando.value = false
  }
}
function claveCopiada() {
  const id = creado.value?.id
  creado.value = null
  panel.value = null
  nuevoNombre.value = ''
  nuevoTipo.value = ''
  if (id) void router.push(`/clientes/${encodeURIComponent(id)}`)
}
function emparejado(clientId: string, nombre: string | null) {
  toast('ok', `${nombre ?? 'El aparato'} ya habla con Fogar`, 'Revisa sus ajustes si quieres cambiarlos.')
  panel.value = null
  void clients.refresh()
  void router.push(`/clientes/${encodeURIComponent(clientId)}`)
}
</script>

<template>
  <div class="clientes">
    <header class="titulo">
      <div class="texto">
        <h1 class="fg-h1">{{ titulo }}</h1>
        <p class="fg-sub">Cada uno entra con su propia clave. Para añadir otro, empareja con un código; no hace falta copiar claves.</p>
      </div>
      <FgBoton :disabled="!!creado" @click="panel = 'crear'">Crear a mano</FgBoton>
      <FgBoton variante="principal" icono="mas" :disabled="!!creado" @click="panel = 'emparejar'">Emparejar un aparato</FgBoton>
    </header>

    <div class="cuerpo">
      <section aria-label="Lista de clientes" class="lista">
        <div class="barra">
          <div class="fg-pestanas" role="tablist" aria-label="Filtrar clientes">
            <button type="button" role="tab" :aria-selected="pestana === 'activos'" @click="pestana = 'activos'">Activos<small>{{ activos }}</small></button>
            <button type="button" role="tab" :aria-selected="pestana === 'desactivados'" @click="pestana = 'desactivados'">Desactivados<small>{{ desactivados }}</small></button>
          </div>
          <label class="buscar">
            <FgIcono nombre="buscar" :tam="16" />
            <span class="fg-solo-lector">Buscar cliente</span>
            <input v-model="busqueda" type="search" placeholder="Buscar por nombre o huella" />
          </label>
        </div>
        <p v-if="clients.error" class="fg-meta">No pude leer los clientes: {{ clients.error }}</p>
        <div v-else-if="filas.length" class="fg-scroll">
          <table class="fg-tabla">
            <thead>
              <tr>
                <th>Aparato</th>
                <th>Tipo</th>
                <th>Responde con</th>
                <th>Ahora</th>
                <th class="num">Huella</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in filas" :key="f.id">
                <td><RouterLink :to="`/clientes/${encodeURIComponent(f.id)}`" class="nombre">{{ f.nombre }}</RouterLink></td>
                <td class="fg-tenue">{{ f.tipo }}</td>
                <td class="fg-tenue">{{ f.responde }}</td>
                <td>
                  <FgEstado v-if="f.conectado" tono="ok" texto="Conectado" />
                  <span v-else class="fg-tenue">Sin sesión</span>
                </td>
                <td class="num fg-tenue">{{ f.huella }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else-if="clients.loaded" class="fg-meta">
          {{ busqueda ? 'Ningún cliente coincide.' : pestana === 'activos' ? 'Todavía no hay ningún aparato. Empareja el primero.' : 'No hay clientes desactivados.' }}
        </p>
        <p class="fg-meta">La huella identifica la clave sin enseñarla. La clave entera solo se ve al crear el cliente o al rotarla.</p>
      </section>

      <aside v-if="panel" class="lado">
        <div v-if="panel === 'emparejar'" class="escena">
          <FondoVivo :calor="0.55" :humo="1" />
          <PanelEmparejar @cerrar="panel = null" @emparejado="emparejado" />
        </div>
        <ClaveNueva
          v-else-if="creado"
          :clave="creado.client_key"
          :nombre="creado.name"
          aviso="El aparato la necesita para entrar. Si la pierdes, rótala desde su ficha."
          @hecho="claveCopiada"
        />
        <form v-else class="crear" novalidate @submit.prevent="crear">
          <h2 class="fg-h2">Crear un cliente a mano</h2>
          <FgCampo v-model="nuevoNombre" etiqueta="Nombre" placeholder="Tele del salón" :error="nuevoError" />
          <div class="campo-tipo">
            <label for="nuevo-tipo">Tipo</label>
            <select id="nuevo-tipo" v-model="nuevoTipo">
              <option value="">Sin tipo</option>
              <option v-for="(texto, valor) in CLIENT_TYPE" :key="valor" :value="valor">{{ texto }}</option>
            </select>
          </div>
          <div class="acciones">
            <FgBoton type="submit" variante="principal" :ocupado="creando">Crear y ver la clave</FgBoton>
            <FgBoton @click="panel = null">Cancelar</FgBoton>
          </div>
        </form>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.clientes {
  display: flex;
  flex-direction: column;
  gap: 32px;
}
.titulo {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px 12px;
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
.barra {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
  border-bottom: 1px solid var(--veta);
}
.barra .fg-pestanas {
  border-bottom: 0;
  flex: 1 1 auto;
}
.buscar {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  margin-bottom: 6px;
  padding: 0 12px;
  border-radius: 10px;
  background: var(--piedra);
  color: var(--hueso-tenue);
  width: 260px;
  max-width: 100%;
}
.buscar:focus-within {
  outline: 2px solid var(--hueso);
  outline-offset: 2px;
}
.buscar input {
  flex: 1;
  min-width: 0;
  border: 0;
  background: none;
  color: var(--hueso);
  outline: none;
  font-size: 14px;
}
.nombre {
  font-weight: 600;
}
.lado {
  flex: 2 1 380px;
  min-width: 0;
}
.escena {
  position: relative;
  padding: 24px;
}
.escena :deep(.fondo) {
  inset: -40px;
  width: calc(100% + 80px);
  height: calc(100% + 80px);
}
.crear {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 22px;
  border-radius: var(--radio-bloque);
  background: var(--piedra);
}
.campo-tipo {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.campo-tipo label {
  font-size: 14px;
  font-weight: 550;
}
.campo-tipo select {
  height: 44px;
  padding: 0 12px;
  border-radius: var(--radio-nodo);
  border: 1px solid var(--veta);
  background: var(--noche);
  color: var(--hueso);
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
  .escena {
    padding: 0;
  }
}
</style>
