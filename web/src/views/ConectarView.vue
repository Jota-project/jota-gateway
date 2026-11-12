<script setup lang="ts">
import { ref, useTemplateRef } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '@/api/http'
import UiButton from '@/components/ui/UiButton.vue'
import UiField from '@/components/ui/UiField.vue'
import UiSwitch from '@/components/ui/UiSwitch.vue'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const session = useSessionStore()

const token = ref('')
const remember = ref(true)
const error = ref('')
const busy = ref(false)
const field = useTemplateRef<InstanceType<typeof UiField>>('field')

const MESSAGES: Partial<Record<ApiError['kind'], string>> = {
  unauthorized: 'Ese token no vale. Revisa el ADMIN_TOKEN de la configuración del gateway.',
  'not-configured': 'El gateway no tiene ADMIN_TOKEN. Ponlo con «fogar-gw config set ADMIN_TOKEN» y reinícialo.',
  network: 'No llego al gateway. Comprueba que está encendido y en esta dirección.',
}

async function submit(): Promise<void> {
  const value = token.value.trim()
  if (busy.value) return
  if (!value) {
    error.value = 'Escribe el token de administrador.'
    field.value?.nudge()
    field.value?.focus()
    return
  }
  busy.value = true
  error.value = ''
  try {
    // Cualquier lectura de /admin sirve para comprobar el token antes de guardarlo.
    await api('/admin/state', { token: value })
    session.signIn(value, remember.value)
    // shortcut: entra en Estado mientras no hay chat; con 17e vuelve a `/`.
    await router.replace('/estado')
  } catch (e) {
    error.value = (e instanceof ApiError && MESSAGES[e.kind]) || 'Algo ha fallado al conectar. Prueba otra vez.'
    field.value?.nudge()
    field.value?.focus()
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="conectar">
    <div class="column">
      <span class="mark j-orb" aria-hidden="true" />
      <div class="intro">
        <h1 class="display">Hola. Conectemos con tu gateway.</h1>
        <p>Necesito el token de administrador para ver el estado y gestionar tus clientes desde aquí.</p>
      </div>

      <form novalidate @submit.prevent="submit">
        <UiField
          id="token"
          ref="field"
          v-model="token"
          label="Token de administrador"
          hint="Es el ADMIN_TOKEN de la configuración del gateway."
          :error="error"
          type="password"
          autocomplete="current-password"
          spellcheck="false"
          autofocus
          @update:model-value="error = ''"
        />

        <UiSwitch v-model="remember">Recordar en este navegador</UiSwitch>

        <UiButton type="submit" variant="primary" class="enter" :busy="busy" :state-key="busy ? 1 : 0">
          {{ busy ? 'Comprobando…' : 'Entrar' }}
        </UiButton>
      </form>
    </div>
  </main>
</template>

<style scoped>
.conectar {
  position: relative;
  z-index: 1;
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 56px 16px;
}

.column {
  width: 100%;
  max-width: 420px;
  display: flex;
  flex-direction: column;
  gap: 32px;
  animation: j-blur-in var(--t-scene) var(--ease-out) both;
}

.mark {
  width: 48px;
  height: 48px;
}

.intro {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

h1 {
  margin: 0;
  line-height: 1.08;
  text-wrap: balance;
}

.intro p {
  margin: 0;
  font-size: 15.5px;
  color: var(--text-soft);
  text-wrap: pretty;
}

form {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 24px;
}

.enter {
  height: 50px;
  margin-top: 4px;
}
</style>
