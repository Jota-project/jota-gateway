import { ref } from 'vue'

// Ayudas de movimiento compartidas. Las curvas y tiempos son tokens de CSS (styles/tokens.css);
// aquí solo lo que necesita JavaScript.

const query = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null

/** `true` si la persona pidió reducir el movimiento. Se actualiza si cambia el ajuste. */
export const reducedMotion = ref(query?.matches ?? false)
query?.addEventListener('change', (event) => (reducedMotion.value = event.matches))

/** La curva --ease-out en JavaScript (Web Animations no lee variables de CSS). */
export const EASE_OUT = 'cubic-bezier(.23, 1, .32, 1)'

/** Vuelve a lanzar la animación de una clase (por ejemplo el «no» de `j-nudge`). */
export function replay(el: Element | null | undefined, className = 'j-nudge'): void {
  if (!el || reducedMotion.value) return
  el.classList.remove(className)
  void (el as HTMLElement).offsetWidth // fuerza el reflow para que la misma animación arranque de nuevo
  el.classList.add(className)
}

/**
 * Anima el ancho de un elemento cuando cambia su contenido (FLIP): `before` en onBeforeUpdate y `after`
 * en onUpdated. Si el ancho no cambia, no hace nada.
 */
export function useFlipWidth(): { before: (el: HTMLElement | null) => void; after: (el: HTMLElement | null) => void } {
  let from = 0
  return {
    before(el) {
      from = el ? el.getBoundingClientRect().width : 0
    },
    after(el) {
      if (!el || !from || reducedMotion.value) return
      const to = el.getBoundingClientRect().width
      if (Math.abs(to - from) < 1) return
      el.animate([{ width: `${from}px` }, { width: `${to}px` }], { duration: 260, easing: EASE_OUT })
    },
  }
}

/** Promesa que se resuelve tras `ms` milisegundos. */
export function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
