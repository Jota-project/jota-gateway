<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { cancelPairing, getPairing, openPairing } from '@/api/admin'
import { ApiError } from '@/api/http'
import type { PairingOpened, PairingState } from '@/api/types'
import FgBoton from './FgBoton.vue'

// Emparejar un aparato: abre un código de 6 cifras (10 min, un solo uso) y espera a que el aparato lo
// canjee. El código solo vive aquí: no se guarda ni se registra. Al cerrar el panel, se anula.
const emit = defineEmits<{ cerrar: []; emparejado: [clientId: string, nombre: string | null] }>()

const abierto = shallowRef<PairingOpened | null>(null)
const estado = ref<PairingState | 'abriendo' | 'error'>('abriendo')
const error = ref('')
const ahora = ref(Date.now())
let sondeo: ReturnType<typeof setInterval> | undefined
let reloj: ReturnType<typeof setInterval> | undefined

async function abrir(): Promise<void> {
  estado.value = 'abriendo'
  try {
    abierto.value = await openPairing()
    estado.value = 'open'
  } catch (e) {
    estado.value = 'error'
    error.value =
      e instanceof ApiError && e.kind === 'conflict'
        ? 'Ya hay 5 códigos abiertos. Espera a que caduquen o anúlalos.'
        : 'No he podido abrir un código. Prueba otra vez.'
  }
}

async function comprobar(): Promise<void> {
  const p = abierto.value
  if (!p || estado.value !== 'open') return
  try {
    const r = await getPairing(p.id)
    estado.value = r.state
    if (r.state === 'used' && r.client_id) emit('emparejado', r.client_id, r.client_name)
  } catch {
    // se reintenta en el siguiente sondeo
  }
}

const restante = computed(() => {
  const p = abierto.value
  if (!p) return 0
  return Math.max(0, Math.round((Date.parse(p.expires_at) - ahora.value) / 1000))
})
const cuenta = computed(() => `${Math.floor(restante.value / 60)}:${String(restante.value % 60).padStart(2, '0')}`)
const progreso = computed(() => `${Math.min(100, (restante.value / 600) * 100)}%`)
const codigo = computed(() => abierto.value?.code ?? '')
const codigoVisible = computed(() => `${codigo.value.slice(0, 3)} ${codigo.value.slice(3)}`)

async function cerrar(): Promise<void> {
  const p = abierto.value
  if (p && estado.value === 'open') void cancelPairing(p.id).catch(() => undefined)
  abierto.value = null
  emit('cerrar')
}

onMounted(() => {
  void abrir()
  sondeo = setInterval(() => void comprobar(), 2000)
  reloj = setInterval(() => {
    ahora.value = Date.now()
    if (estado.value === 'open' && restante.value === 0) estado.value = 'expired'
  }, 1000)
})
onBeforeUnmount(() => {
  clearInterval(sondeo)
  clearInterval(reloj)
  const p = abierto.value
  if (p && estado.value === 'open') void cancelPairing(p.id).catch(() => undefined)
})
</script>

<template>
  <section class="emparejar" aria-labelledby="h-emp">
    <div class="cab">
      <h2 id="h-emp" class="fg-h2">Emparejar un aparato</h2>
      <FgBoton tam="sm" @click="cerrar">{{ estado === 'open' ? 'Cancelar' : 'Cerrar' }}</FgBoton>
    </div>
    <template v-if="estado === 'open' || estado === 'abriendo'">
      <p class="fg-suave">Escribe este código en el aparato nuevo, o díselo en voz alta si te lo pide.</p>
      <p class="codigo" :aria-label="`Código ${codigo.split('').join(' ')}`">{{ estado === 'abriendo' ? '··· ···' : codigoVisible }}</p>
      <div class="tiempo">
        <div class="pista" aria-hidden="true"><div class="barra" :style="{ width: progreso }" /></div>
        <p class="fg-meta pie"><span>Esperando al aparato…</span><span>caduca en {{ cuenta }}</span></p>
      </div>
      <p class="fg-meta nota">El aparato pondrá su nombre y su tipo; después puedes cambiarlos. Un código vale una sola vez.</p>
    </template>
    <template v-else-if="estado === 'expired' || estado === 'cancelled'">
      <p class="fg-suave">El código ha caducado sin que ningún aparato lo usara.</p>
      <FgBoton variante="principal" @click="abrir">Otro código</FgBoton>
    </template>
    <template v-else-if="estado === 'error'">
      <p class="fg-suave">{{ error }}</p>
      <FgBoton @click="abrir">Probar otra vez</FgBoton>
    </template>
    <p v-else class="fg-suave">Emparejado.</p>
  </section>
</template>

<style scoped>
.emparejar {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 24px;
  border-radius: var(--radio-vidrio);
  background: rgb(11 16 18 / 0.36);
  -webkit-backdrop-filter: blur(20px) saturate(1.25);
  backdrop-filter: blur(20px) saturate(1.25);
  box-shadow: var(--sombra-vidrio);
}
.emparejar p {
  margin: 0;
}
.cab {
  display: flex;
  align-items: center;
  gap: 12px;
}
.cab h2 {
  flex: 1;
}
.codigo {
  font: 500 56px/1 var(--font-texto);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.06em;
}
.tiempo {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.pista {
  height: 4px;
  border-radius: 4px;
  background: var(--veta);
}
.barra {
  height: 4px;
  border-radius: 4px;
  background: var(--hueso);
  transition: width 1s linear;
}
.pie {
  display: flex;
  justify-content: space-between;
}
.nota {
  padding-top: 14px;
  border-top: 1px solid var(--veta);
}
</style>
