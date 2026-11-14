<script setup lang="ts">
import { computed, useTemplateRef } from 'vue'
import type { OpenClawAccess } from '@/api/types'
import { copyText } from '@/lib/clipboard'
import { describeAccess } from '@/lib/openclawAccess'
import { toast } from '@/lib/toasts'
import FgBoton from './FgBoton.vue'
import FgEstado from './FgEstado.vue'

// Con qué acceso está el gateway en OpenClaw: permisos, cómo entra y si falta aprobarlo. Nunca hay
// secretos aquí (ni el token ni la clave privada llegan al panel); el id de dispositivo es público.
const props = defineProps<{ access: Partial<OpenClawAccess> | undefined }>()
const vista = computed(() => describeAccess(props.access))
const dispositivo = computed(() => props.access?.device_id ?? null)

const campo = useTemplateRef<HTMLTextAreaElement>('campo')
async function copiar(): Promise<void> {
  const comando = vista.value?.comando
  if (!comando) return
  if (await copyText(comando, campo.value)) toast('ok', 'Comando copiado', 'Pégalo en la máquina de OpenClaw.')
  else toast('warn', 'No he podido copiarlo', 'Está seleccionado: cópialo con el teclado.')
}
</script>

<template>
  <div v-if="vista" class="acceso">
    <FgEstado :tono="vista.tono" :texto="vista.titulo" class="cabeza" />
    <p v-if="vista.detalle" class="fg-suave">{{ vista.detalle }}</p>
    <div v-if="vista.comando" class="comando">
      <textarea ref="campo" :value="vista.comando" rows="2" readonly aria-label="Comando para aprobar el gateway" spellcheck="false" @focus="campo?.select()" />
      <FgBoton tam="sm" variante="principal" icono="copiar" @click="copiar">Copiar</FgBoton>
    </div>
    <dl>
      <div>
        <dt>Permisos</dt>
        <dd v-if="vista.permisos" class="permisos">
          <FgEstado v-for="p in vista.permisos" :key="p.texto" :tono="p.concedido ? 'ok' : 'off'" :tam="12">
            <span :class="{ 'fg-tenue': !p.concedido }">{{ p.texto }}<span class="fg-solo-lector">{{ p.concedido ? ': sí' : ': no' }}</span></span>
          </FgEstado>
          <span v-if="vista.otros.length" class="fg-tenue">y {{ vista.otros.join(', ') }}</span>
        </dd>
        <dd v-else class="fg-tenue">Desconocidos</dd>
      </div>
      <div v-if="vista.metodo">
        <dt>Entra con</dt>
        <dd>{{ vista.metodo }}</dd>
      </div>
      <div v-if="dispositivo">
        <dt>Dispositivo</dt>
        <dd><code :title="dispositivo">{{ dispositivo.slice(0, 16) }}…</code></dd>
      </div>
    </dl>
  </div>
  <p v-else class="fg-meta">Este gateway no dice con qué acceso está en OpenClaw.</p>
</template>

<style scoped>
.acceso {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 18px;
  border-radius: var(--radio-bloque);
  background: var(--piedra);
}
.acceso p {
  margin: 0;
}
.cabeza {
  font-weight: 600;
}
.comando {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
/* El comando entero a la vista (parte en dos líneas si no cabe): es lo que hay que pegar. */
.comando textarea {
  flex: 1 1 100%;
  min-width: 0;
  padding: 8px 12px;
  resize: none;
  word-break: break-all;
  border: 0;
  border-radius: 10px;
  background: var(--noche);
  color: var(--hueso);
  font: 400 13px ui-monospace, SFMono-Regular, Menlo, monospace;
}
dl {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 14px;
}
dl > div {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 12px;
}
dt {
  color: var(--hueso-tenue);
}
dd {
  margin: 0;
}
.permisos {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
}
code {
  color: var(--hueso-suave);
}
</style>
