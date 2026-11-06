<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '@/api/http'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const session = useSessionStore()

const token = ref('')
const remember = ref(true)
const error = ref('')
const busy = ref(false)

const MESSAGES: Partial<Record<ApiError['kind'], string>> = {
  unauthorized: 'Ese token no vale. Revisa el que tienes en ADMIN_TOKEN.',
  'not-configured': 'El gateway no tiene ADMIN_TOKEN configurado: ponlo en su configuración y reinícialo.',
  network: 'No llego al gateway. ¿Está encendido?',
}

async function submit(): Promise<void> {
  const value = token.value.trim()
  if (!value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    // Cualquier lectura de /admin sirve para comprobar el token antes de guardarlo.
    await api('/admin/state', { token: value })
    session.signIn(value, remember.value)
    await router.replace('/')
  } catch (e) {
    error.value = (e instanceof ApiError && MESSAGES[e.kind]) || 'Algo ha fallado al conectar. Prueba otra vez.'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="conectar">
    <div class="column">
      <div class="orb" aria-hidden="true" />
      <div class="intro">
        <h1 class="display">Hola. Conectemos con tu gateway.</h1>
        <p>Necesito el token de administrador para ver el estado y hablar contigo desde aquí.</p>
      </div>

      <form @submit.prevent="submit">
        <div class="field-group">
          <label for="token">Token de administrador</label>
          <input
            id="token"
            v-model="token"
            class="field"
            :class="{ invalid: error }"
            type="password"
            autocomplete="current-password"
            :aria-invalid="Boolean(error)"
            aria-describedby="token-help"
            autofocus
          >
          <span v-if="error" role="alert" class="error">{{ error }}</span>
          <span id="token-help" class="help">Es el ADMIN_TOKEN de la configuración del gateway.</span>
        </div>

        <label class="switch-row">
          <span>Recordar en este navegador</span>
          <input v-model="remember" type="checkbox" role="switch" class="switch">
        </label>

        <button type="submit" class="primary" :disabled="!token.trim() || busy">
          {{ busy ? 'Comprobando…' : 'Entrar' }}
        </button>
      </form>
    </div>
  </main>
</template>

<style scoped>
.conectar {
  position: relative;
  z-index: 1;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 56px 20px;
}

.column {
  width: 100%;
  max-width: 440px;
  display: flex;
  flex-direction: column;
  gap: 32px;
}

.orb {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #a99ad1, var(--malva) 55%, var(--cielo));
  box-shadow:
    0 0 0 10px rgb(124 106 174 / 0.16),
    0 0 60px rgb(124 106 174 / 0.5);
  animation: breathe 3.4s ease-in-out infinite;
}

@keyframes breathe {
  50% {
    transform: scale(1.05);
  }
}

.intro {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

h1 {
  margin: 0;
  font-size: 40px;
  line-height: 1.08;
}

.intro p {
  margin: 0;
  font-size: 15.5px;
  color: var(--text-soft);
}

form {
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.field-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

label {
  font-size: 14px;
  font-weight: 500;
}

.field {
  height: 48px;
  padding: 0 16px;
  border: 0;
  border-radius: var(--r-m);
  background: rgb(246 243 240 / 0.06);
  color: var(--text);
  font-size: 15px;
  outline: none;
}

.field:focus {
  background: rgb(246 243 240 / 0.09);
  box-shadow: 0 0 0 2px var(--accent);
}

.field.invalid {
  box-shadow: 0 0 0 2px var(--bad);
}

.error {
  font-size: 13.5px;
  color: var(--bad);
}

.help {
  font-size: 13px;
  color: var(--text-faint);
}

.switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  font-size: 14.5px;
  font-weight: 400;
  cursor: pointer;
}

.switch {
  appearance: none;
  flex: 0 0 auto;
  width: 48px;
  height: 28px;
  margin: 0;
  border-radius: 999px;
  background: var(--switch-off);
  position: relative;
  cursor: pointer;
  transition: background 0.2s var(--ease);
}

.switch::after {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.2s var(--ease);
}

.switch:checked {
  background: var(--accent);
}

.switch:checked::after {
  transform: translateX(20px);
}

.primary {
  height: 50px;
  border: 0;
  border-radius: 999px;
  background: var(--accent);
  color: var(--on-accent);
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: var(--play-shadow);
  transition:
    transform 0.15s var(--ease),
    filter 0.15s;
}

.primary:hover:not(:disabled) {
  transform: translateY(-1px);
  filter: brightness(1.08);
}

.primary:disabled {
  opacity: 0.45;
  cursor: default;
  box-shadow: none;
}
</style>
