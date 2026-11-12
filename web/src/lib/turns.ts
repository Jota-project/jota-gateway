import type { PipelineEventRecord } from '@/api/types'

export interface Etapa {
  stage: string
  /** Desde el principio del turno, en ms. */
  hasta: number
  /** Donde empieza su barra (fin de la etapa anterior), en ms desde el principio del turno. */
  desde: number
}

export interface Turno {
  numero: number
  total: number
  etapas: Etapa[]
}

// Las parciales no aportan nada en la vista por turno y el fin de sesión no es de ningún turno.
const FUERA = new Set(['transcription_partial', 'session_end'])

/** Agrupa los eventos de una sesión en turnos con sus etapas en el tiempo (para las barras). */
export function turnos(events: PipelineEventRecord[]): Turno[] {
  const grupos = new Map<number, PipelineEventRecord[]>()
  for (const e of events) {
    if (FUERA.has(e.stage)) continue
    const lista = grupos.get(e.turn) ?? []
    lista.push(e)
    grupos.set(e.turn, lista)
  }
  return [...grupos.entries()]
    .filter(([, lista]) => lista.length > 0)
    .sort((a, b) => a[0] - b[0])
    .map(([numero, lista]) => {
      const orden = [...lista].sort((a, b) => a.ts_ms - b.ts_ms)
      const t0 = orden[0]!.ts_ms
      let previo = 0
      const etapas = orden.map((e) => {
        const hasta = e.ts_ms - t0
        const etapa = { stage: e.stage, desde: previo, hasta }
        previo = hasta
        return etapa
      })
      return { numero, total: previo, etapas }
    })
}
