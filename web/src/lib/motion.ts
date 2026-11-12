import { ref } from 'vue'

const query = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null

/** `true` si la persona pidió reducir el movimiento. Se actualiza si cambia el ajuste. */
export const reducedMotion = ref(query?.matches ?? false)
query?.addEventListener('change', (event) => (reducedMotion.value = event.matches))
