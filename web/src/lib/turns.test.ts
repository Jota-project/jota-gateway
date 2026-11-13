import { describe, expect, it } from 'vitest'
import { turnos } from './turns'

const ev = (stage: string, ts_ms: number, turn: number) => ({ stage, ts_ms, elapsed_ms: 0, turn, meta: {} })

describe('turnos', () => {
  it('agrupa por turno y encadena las barras desde el principio del turno', () => {
    const r = turnos([
      ev('llm_start', 1000, 1),
      ev('llm_first_token', 1900, 1),
      ev('llm_done', 3100, 1),
      ev('llm_start', 9000, 2),
      ev('session_end', 9500, 2),
    ])
    expect(r.map((t) => t.numero)).toEqual([1, 2])
    expect(r[0]!.total).toBe(2100)
    expect(r[0]!.etapas[1]).toEqual({ stage: 'llm_first_token', desde: 0, hasta: 900 })
    expect(r[0]!.etapas[2]).toEqual({ stage: 'llm_done', desde: 900, hasta: 2100 })
    expect(r[1]!.etapas.map((e) => e.stage)).toEqual(['llm_start'])
  })
})
