import { describe, expect, it } from 'vitest'
import { createApp, h, nextTick, reactive } from 'vue'
import MensajeConversacion from './MensajeConversacion.vue'

// Montado a mano (sin @vue/test-utils): lo que importa es el DOM que lee el lector de pantalla.
interface Props {
  quien: string
  propio: boolean
  texto: string
  escribiendo?: boolean
  progreso?: string | null
}

function mount(props: Partial<Props>) {
  const state = reactive<Props>({ quien: 'Fogar', propio: false, texto: '', ...props })
  const el = document.createElement('div')
  createApp({ render: () => h(MensajeConversacion, state) }).mount(el)
  return { el, state }
}

describe('MensajeConversacion: el progreso', () => {
  it('la región viva está desde el principio y solo cambia su texto', async () => {
    const { el, state } = mount({ escribiendo: true, progreso: null })
    const live = el.querySelector('[aria-live="polite"]')
    expect(live).not.toBeNull()
    expect(live!.textContent).toBe('')

    state.progreso = 'Pensando…'
    await nextTick()
    expect(el.querySelector('[aria-live="polite"]')).toBe(live) // el mismo nodo, no uno nuevo
    expect(live!.textContent).toBe('Pensando…')
    expect(el.querySelector('.progreso')!.textContent).toBe('Pensando…')

    state.progreso = 'Usando ha_call_service…'
    await nextTick()
    expect(live!.textContent).toBe('Usando ha_call_service…')
  })

  it('al terminar la respuesta se vacía y la línea visible desaparece', async () => {
    const { el, state } = mount({ escribiendo: true, progreso: 'Pensando…' })
    state.escribiendo = false
    await nextTick()
    expect(el.querySelector('[aria-live="polite"]')!.textContent).toBe('')
    expect(el.querySelector('.progreso')).toBeNull()
  })
})
