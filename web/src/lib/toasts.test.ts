import { beforeEach, describe, expect, it } from 'vitest'
import { clearToasts, dismissToast, MAX_TOASTS, toast, toasts } from './toasts'

describe('toasts', () => {
  beforeEach(() => clearToasts())

  it('pone el más nuevo delante', () => {
    toast('ok', 'Primero')
    toast('warn', 'Segundo', 'con detalle')
    expect(toasts.map((t) => t.title)).toEqual(['Segundo', 'Primero'])
    expect(toasts[0]).toMatchObject({ tone: 'warn', body: 'con detalle' })
  })

  it(`no guarda más de ${MAX_TOASTS}: se va el más antiguo`, () => {
    for (let i = 1; i <= MAX_TOASTS + 2; i++) toast('ok', `Aviso ${i}`)
    expect(toasts).toHaveLength(MAX_TOASTS)
    expect(toasts.at(-1)?.title).toBe('Aviso 3')
  })

  it('quita uno por su id y no falla con un id que ya no está', () => {
    const a = toast('ok', 'A')
    const b = toast('bad', 'B')
    dismissToast(a)
    dismissToast(a)
    expect(toasts.map((t) => t.id)).toEqual([b])
  })

  it('da ids distintos a cada aviso', () => {
    const ids = [toast('ok', 'x'), toast('ok', 'y'), toast('ok', 'z')]
    expect(new Set(ids).size).toBe(3)
  })
})
