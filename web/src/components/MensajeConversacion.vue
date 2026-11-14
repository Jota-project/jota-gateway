<script setup lang="ts">
// Un mensaje de una conversación: quién, cuándo y el texto tal cual (nunca HTML). Lo de la persona va
// en un bloque de piedra; lo de Fogar, sobre la noche. Una respuesta que llega lleva el cursor al final;
// una que no terminó bien dice por qué debajo.
defineProps<{
  quien: string
  propio: boolean
  texto: string
  hora?: string | null
  /** Fogar habló sin que le preguntaran (push). */
  push?: boolean
  escribiendo?: boolean
  /** En qué está mientras escribe (`turn_progress`), en una frase corta. */
  progreso?: string | null
  /** Por qué no terminó (motivo de `turn_end`). */
  aviso?: string | null
}>()
</script>

<template>
  <article class="mensaje" :class="{ propio, push }">
    <p class="cab fg-meta">
      <span class="quien">{{ quien }}</span><template v-if="push"> · sin que le preguntaras</template><template v-if="hora"> · <time>{{ hora }}</time></template>
    </p>
    <p v-if="texto || escribiendo" class="texto" :class="{ escribiendo }">{{ texto }}</p>
    <p v-else-if="!aviso" class="texto fg-tenue">(sin texto)</p>
    <p v-if="escribiendo && progreso" class="progreso fg-meta" aria-hidden="true">{{ progreso }}</p>
    <!-- La región viva está siempre en el DOM y solo cambia su texto: así el lector de pantalla
         anuncia cada cambio (una creada con su texto ya puesto no se anuncia). -->
    <span class="fg-solo-lector" aria-live="polite">{{ escribiendo && progreso ? progreso : '' }}</span>
    <p v-if="aviso" class="aviso">{{ aviso }}</p>
  </article>
</template>

<style scoped>
.mensaje {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 72ch;
}
.propio {
  align-self: flex-start;
  padding: 12px 16px;
  border-radius: var(--radio-bloque);
  background: var(--piedra-alta);
}
/* Fogar empezó a hablar: la brasa junto a su nombre. */
.push .quien::before {
  content: '';
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 7px;
  vertical-align: 1px;
  border-radius: 50%;
  background: var(--llama);
}
.cab {
  margin: 0;
}
.quien {
  color: var(--hueso-suave);
  font-weight: 550;
}
.texto {
  margin: 0;
  font-size: 16px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.escribiendo::after {
  content: '';
  display: inline-block;
  width: 8px;
  height: 1.05em;
  margin-left: 2px;
  vertical-align: -0.15em;
  border-radius: 2px;
  background: var(--hueso-suave);
  animation: latido 1s steps(2, start) infinite;
}
@keyframes latido {
  to {
    visibility: hidden;
  }
}
/* En qué está Fogar antes de escribir: una línea tenue bajo el cursor, sin ocupar su sitio. */
.progreso {
  overflow-wrap: anywhere;
}
.aviso {
  margin: 0;
  font-size: 13px;
  color: var(--hueso-suave);
}
.aviso::before {
  content: '';
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-right: 7px;
  vertical-align: 0;
  border: 1.5px solid var(--degradado);
  transform: rotate(45deg) scale(0.85);
}
</style>
