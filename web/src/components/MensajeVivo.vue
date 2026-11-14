<script setup lang="ts">
import { computed } from 'vue'
import { endReasonText, progressText, type ChatLine } from '@/lib/chat'
import { formatClock } from '@/lib/format'
import MensajeConversacion from './MensajeConversacion.vue'

// Una línea del chat en vivo. Cada una calcula lo suyo: un token nuevo solo vuelve a pintar la
// respuesta que lo recibe, no toda la conversación.
const props = defineProps<{ linea: ChatLine; ahora: Date }>()

const aviso = computed(() => {
  const l = props.linea
  if (l.unsent) return 'No se envió: no se pudo reabrir la conversación.'
  if (l.state !== 'ended') return null
  return [l.otherConversation ? 'Es de otra conversación del panel.' : null, endReasonText(l)].filter(Boolean).join(' ') || null
})
// Mientras Fogar no escribe: en qué está («Pensando…», «Usando …»).
const progreso = computed(() => {
  const l = props.linea
  return l.state === 'streaming' && l.progress ? progressText(l.progress) : null
})
const hora = computed(() => (props.linea.at ? formatClock(props.linea.at, props.ahora) : null))
</script>

<template>
  <MensajeConversacion
    :quien="linea.role === 'user' ? 'Tú' : 'Fogar'"
    :propio="linea.role === 'user'"
    :texto="linea.text"
    :hora="hora"
    :push="linea.origin === 'push'"
    :escribiendo="linea.state === 'streaming'"
    :progreso="progreso"
    :aviso="aviso"
  />
</template>
