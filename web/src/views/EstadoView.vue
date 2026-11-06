<script setup lang="ts">
import { computed } from 'vue'
import { formatDateTime, formatDuration } from '@/lib/format'
import { PROVIDER, SERVICE_NAME, SERVICE_STATE } from '@/lib/labels'
import { useLiveStore } from '@/stores/live'

// Primer corte de la pantalla Estado (17b): titular, encendido y servicios desde el flujo vivo.
// Faltan «Reconectar», «Comprobar servicios», la tabla de conectados y los totales.
const live = useLiveStore()

const headline = computed(() => {
  const s = live.state?.services
  if (live.connection === 'offline') return 'No llego al gateway'
  if (!s) return 'Conectando…'
  if (s.orchestrator.state !== 'CONNECTED') return 'OpenClaw se está reconectando'
  if (s.stt.state !== 'CONNECTED' || s.tts.state !== 'CONNECTED') return 'La voz tiene problemas'
  return 'Todo funciona'
})

const services = computed(() => {
  const s = live.state?.services
  if (!s) return []
  return [
    { key: 'orchestrator', name: SERVICE_NAME.orchestrator, sub: 'orquestador', ...s.orchestrator },
    { key: 'stt', name: SERVICE_NAME.stt, sub: PROVIDER[s.stt.provider] ?? s.stt.provider, ...s.stt },
    { key: 'tts', name: SERVICE_NAME.tts, sub: PROVIDER[s.tts.provider] ?? s.tts.provider, ...s.tts },
  ]
})
</script>

<template>
  <div class="estado">
    <header>
      <h1 class="display">{{ headline }}</h1>
      <p v-if="live.state" class="tab">
        Encendido el {{ formatDateTime(live.state.gateway.started_at) }}, hace
        {{ formatDuration(live.state.gateway.uptime_s) }}.
      </p>
    </header>

    <section v-if="services.length" aria-labelledby="h-servicios">
      <h2 id="h-servicios">Servicios</h2>
      <div class="services">
        <div v-for="service in services" :key="service.key" class="service">
          <div class="service-name">
            <span>{{ service.name }}</span>
            <span class="sub">{{ service.sub }}</span>
          </div>
          <span class="state" :class="service.state.toLowerCase()">
            <span class="dot" aria-hidden="true" />{{ SERVICE_STATE[service.state] }}
          </span>
          <dl>
            <dt>Último error</dt>
            <dd>{{ service.last_error ?? 'Ninguno' }}</dd>
          </dl>
        </div>
      </div>
    </section>

    <section v-if="live.state" aria-labelledby="h-conectados">
      <h2 id="h-conectados">Conectados ahora</h2>
      <p class="tab soft">
        {{ live.state.totals.sessions_active }}
        {{ live.state.totals.sessions_active === 1 ? 'sesión' : 'sesiones' }}
      </p>
    </section>
  </div>
</template>

<style scoped>
.estado {
  display: flex;
  flex-direction: column;
  gap: 48px;
}

header {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

h1 {
  margin: 0;
  font-size: 44px;
  line-height: 1.05;
}

header p {
  margin: 0;
  font-size: 16px;
  color: var(--text-soft);
}

section {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

h2 {
  margin: 0;
  font-family: var(--serif);
  font-size: 24px;
  font-weight: 400;
  font-variation-settings: 'opsz' 72;
  letter-spacing: -0.01em;
}

.services {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 40px;
}

.service {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.service-name {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 17px;
  font-weight: 600;
}

.sub {
  font-size: 13px;
  font-weight: 400;
  color: var(--text-faint);
}

.state {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-soft);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--status);
}

.state.reconnecting {
  color: var(--warn);
}

.state.reconnecting .dot {
  background: var(--warn);
}

.state.degraded {
  color: var(--bad);
}

.state.degraded .dot {
  background: var(--bad);
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
  color: var(--text-soft);
}

.soft {
  margin: 0;
  color: var(--text-soft);
}
</style>
