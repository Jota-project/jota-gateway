<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, useTemplateRef } from 'vue'
import { copyText } from '@/lib/clipboard'
import { toast } from '@/lib/toasts'
import FgBoton from './FgBoton.vue'

// La clave de un cliente, la única vez que se ve (crear y rotar). No se guarda en ningún sitio: vive en
// este componente y se va con él. Mientras está a la vista, salir de la página pide confirmación.
const props = defineProps<{ clave: string; nombre: string; aviso?: string }>()
const emit = defineEmits<{ hecho: [] }>()

const campo = useTemplateRef<HTMLInputElement>('campo')
const copiada = ref(false)

async function copiar(): Promise<void> {
  if (await copyText(props.clave, campo.value)) {
    copiada.value = true
    toast('ok', 'Clave copiada')
  } else toast('warn', 'No he podido copiarla', 'Está seleccionada: cópiala con el teclado.')
}

const antesDeSalir = (e: BeforeUnloadEvent) => e.preventDefault()
onMounted(() => window.addEventListener('beforeunload', antesDeSalir))
onBeforeUnmount(() => window.removeEventListener('beforeunload', antesDeSalir))
</script>

<template>
  <section class="clave" role="alert" aria-labelledby="h-clave">
    <h2 id="h-clave" class="fg-h2">Clave de «{{ nombre }}». Cópiala ahora: no se vuelve a enseñar.</h2>
    <p v-if="aviso" class="fg-suave">{{ aviso }}</p>
    <div class="fila">
      <input ref="campo" class="valor" :value="clave" readonly aria-label="Clave del cliente" spellcheck="false" @focus="campo?.select()" />
      <FgBoton variante="principal" icono="copiar" @click="copiar">Copiar</FgBoton>
      <FgBoton @click="emit('hecho')">{{ copiada ? 'Hecho' : 'Ya la he copiado' }}</FgBoton>
    </div>
  </section>
</template>

<style scoped>
.clave {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px;
  border-radius: var(--radio-bloque);
  background: var(--piedra-alta);
}
.clave p {
  margin: 0;
}
.fila {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}
.valor {
  flex: 1 1 320px;
  min-width: 0;
  height: 44px;
  padding: 0 14px;
  border: 0;
  border-radius: 10px;
  background: var(--noche);
  color: var(--hueso);
  font: 400 14px ui-monospace, SFMono-Regular, Menlo, monospace;
}
</style>
