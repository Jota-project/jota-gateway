<script setup lang="ts">
import { computed } from 'vue'
import { formatDuration } from '@/lib/format'
import { SERVICE_NAME, SERVICE_STATE } from '@/lib/labels'
import { useLiveStore } from '@/stores/live'

// Regla del mando de JScreen: el estado solo se escribe cuando algo va mal.
const live = useLiveStore()

const rows = computed(() => {
  const services = live.state?.services
  if (!services) return []
  return (['orchestrator', 'stt', 'tts'] as const).map((key) => {
    const state = services[key].state
    return {
      key,
      name: SERVICE_NAME[key],
      ok: state === 'CONNECTED',
      label: state === 'CONNECTED' ? '' : SERVICE_STATE[state],
      tone: state === 'DEGRADED' ? 'bad' : 'warn',
    }
  })
})
</script>

<template>
  <div class="health" aria-live="polite">
    <p v-if="live.connection === 'offline'" class="offline">Sin conexión con el gateway</p>
    <template v-else-if="live.state">
      <p class="uptime tab">Encendido hace {{ formatDuration(live.state.gateway.uptime_s) }}</p>
      <ul>
        <li v-for="row in rows" :key="row.key">
          <span class="dot" :class="row.ok ? 'ok' : row.tone" aria-hidden="true" />
          {{ row.name }}
          <span v-if="row.label" class="label" :class="row.tone">{{ row.label }}</span>
        </li>
      </ul>
    </template>
    <p v-else class="uptime">Conectando…</p>
  </div>
</template>

<style scoped>
.health {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0 8px;
  font-size: 13px;
}

p {
  margin: 0;
}

.uptime {
  color: var(--text-faint);
}

.offline {
  color: var(--warn);
}

ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--text-soft);
}

li {
  display: flex;
  align-items: center;
  gap: 10px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.dot.ok {
  background: var(--status);
}

.dot.warn {
  background: var(--warn);
}

.dot.bad {
  background: var(--bad);
}

.label {
  margin-left: auto;
}

.label.warn {
  color: var(--warn);
}

.label.bad {
  color: var(--bad);
}
</style>
