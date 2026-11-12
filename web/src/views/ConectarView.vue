<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '@/api/http'
import FgBoton from '@/components/FgBoton.vue'
import FgCampo from '@/components/FgCampo.vue'
import FondoVivo from '@/components/FondoVivo.vue'
import LineaViva from '@/components/LineaViva.vue'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const session = useSessionStore()

const token = ref('')
const recordar = ref(true)
const error = ref<string | null>(null)
const ocupado = ref(false)

const MENSAJES: Partial<Record<ApiError['kind'], string>> = {
  unauthorized: 'Ese token no vale. Revísalo y vuelve a probar.',
  'not-configured': 'El gateway no tiene token de administrador. Ponlo con «fogar-gw config set ADMIN_TOKEN» y reinícialo.',
  network: 'No llego al gateway. Comprueba que está encendido y en esta dirección.',
}

async function entrar(): Promise<void> {
  const valor = token.value.trim()
  if (ocupado.value) return
  if (!valor) {
    error.value = 'Escribe el token de administrador.'
    return
  }
  ocupado.value = true
  error.value = null
  try {
    // Cualquier lectura de /admin sirve para comprobar el token antes de guardarlo.
    await api('/admin/state', { token: valor })
    session.signIn(valor, recordar.value)
    await router.replace('/')
  } catch (e) {
    error.value = (e instanceof ApiError && MENSAJES[e.kind]) || 'Algo ha fallado al conectar. Prueba otra vez.'
  } finally {
    ocupado.value = false
  }
}
</script>

<template>
  <div class="conectar">
    <section class="marca" aria-label="Fogar">
      <FondoVivo :calor="0.45" :humo="0.9" />
      <div class="linea"><LineaViva etiqueta="Fogar" /></div>
      <p class="fg-suave lema">El gateway de tu casa. Desde aquí ves si todo responde y gestionas los aparatos que hablan con Fogar.</p>
    </section>
    <main class="lado">
      <form novalidate @submit.prevent="entrar">
        <div class="intro">
          <h1 class="fg-h1">Hola. Conectemos con tu gateway.</h1>
          <p class="fg-suave">Pon el token de administrador. Lo ves en el gateway con <code>fogar-gw config get ADMIN_TOKEN</code>.</p>
        </div>
        <FgCampo v-model="token" etiqueta="Token de administrador" type="password" autocomplete="current-password" :error="error" />
        <label class="recordar">
          <input v-model="recordar" type="checkbox" role="switch" />
          <span>Recordar en este navegador</span>
        </label>
        <FgBoton type="submit" variante="principal" class="entrar" :ocupado="ocupado">Entrar</FgBoton>
      </form>
    </main>
  </div>
</template>

<style scoped>
.conectar {
  min-height: 100vh;
  display: flex;
  flex-wrap: wrap;
  overflow: hidden;
}
.marca {
  position: relative;
  flex: 1 1 520px;
  min-height: 420px;
  padding: 64px 72px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 48px;
}
.marca :deep(.fondo) {
  inset: -10% -20% -30% -30%;
  width: 150%;
  height: 140%;
}
.linea {
  position: relative;
  width: min(520px, 100%);
  margin-left: -6%;
}
.lema {
  position: relative;
  margin: 0;
  max-width: 380px;
  font-size: 14px;
}
.lado {
  position: relative;
  flex: 1 1 440px;
  padding: 64px 72px;
  display: flex;
  align-items: center;
}
form {
  width: 100%;
  max-width: 420px;
  display: flex;
  flex-direction: column;
  gap: 22px;
}
.intro {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.intro p {
  margin: 0;
}
.intro code {
  color: var(--hueso);
}
.recordar {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 44px;
  color: var(--hueso-suave);
  cursor: pointer;
}
.recordar input {
  appearance: none;
  position: relative;
  flex: none;
  width: 40px;
  height: 24px;
  margin: 0;
  border-radius: 999px;
  background: var(--piedra-alta);
  box-shadow: inset 0 0 0 1px var(--veta);
  cursor: pointer;
  transition: background-color var(--t-rapido) ease;
}
.recordar input::after {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  background: var(--hueso-suave);
  transition:
    transform var(--t-base) var(--ease-out),
    background-color var(--t-rapido) ease;
}
.recordar input:checked {
  background: var(--hueso);
}
.recordar input:checked::after {
  transform: translateX(16px);
  background: var(--noche);
}
.entrar {
  height: 48px;
  font-size: 16px;
}
@media (max-width: 760px) {
  .marca {
    min-height: 0;
    padding: 32px 16px 8px;
    gap: 16px;
  }
  .linea {
    width: 260px;
  }
  .lema {
    display: none;
  }
  .lado {
    padding: 8px 16px 40px;
  }
}
</style>
