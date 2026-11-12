import { describe, expect, it } from 'vitest'
import { createSseParser } from './stateStream'

function collect(chunks: string[]): string[] {
  const out: string[] = []
  const parse = createSseParser((data) => out.push(data))
  chunks.forEach(parse)
  return out
}

describe('createSseParser', () => {
  it('entrega cada evento completo', () => {
    expect(collect(['data: {"a":1}\n\ndata: {"a":2}\n\n'])).toEqual(['{"a":1}', '{"a":2}'])
  })

  it('aguanta un evento partido entre trozos', () => {
    expect(collect(['data: {"a"', ':1}\n', '\n'])).toEqual(['{"a":1}'])
  })

  it('aguanta saltos de línea \\r\\n', () => {
    expect(collect(['data: x\r\n\r\n'])).toEqual(['x'])
  })

  it('une varias líneas data del mismo evento', () => {
    expect(collect(['data: uno\ndata: dos\n\n'])).toEqual(['uno\ndos'])
  })

  it('ignora eventos sin data y no entrega uno a medias', () => {
    expect(collect([': latido\n\n', 'data: a medias'])).toEqual([])
  })
})
