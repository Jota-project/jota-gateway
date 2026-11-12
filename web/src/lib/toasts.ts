import { reactive } from 'vue'

// Avisos de la web: cualquier sitio llama a `toast()` y UiToaster (montado una vez en App.vue) los pinta.

export type ToastTone = 'ok' | 'warn' | 'bad'

export interface Toast {
  id: number
  tone: ToastTone
  title: string
  body?: string
}

/** Como mucho se ven cuatro: al llegar el quinto, se va el más antiguo. */
export const MAX_TOASTS = 4

let nextId = 1

/** Los avisos abiertos, del más nuevo al más antiguo. */
export const toasts = reactive<Toast[]>([])

export function toast(tone: ToastTone, title: string, body?: string): number {
  const item: Toast = { id: nextId++, tone, title, body }
  toasts.unshift(item)
  if (toasts.length > MAX_TOASTS) toasts.splice(MAX_TOASTS)
  return item.id
}

export function dismissToast(id: number): void {
  const index = toasts.findIndex((item) => item.id === id)
  if (index >= 0) toasts.splice(index, 1)
}

export function clearToasts(): void {
  toasts.splice(0)
}
