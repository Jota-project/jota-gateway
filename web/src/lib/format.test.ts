import { describe, expect, it } from 'vitest'
import { formatDuration, formatModes, formatSeconds } from './format'

describe('formatDuration', () => {
  it.each([
    [0, '0 s'],
    [45.9, '45 s'],
    [60, '1 min'],
    [3599, '59 min'],
    [3600, '1 h'],
    [15_120, '4 h 12 min'],
    [86_400, '1 d'],
    [274_320, '3 d 4 h'],
    [-5, '0 s'],
  ])('%s s → %s', (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected)
  })
})

describe('formatModes y formatSeconds', () => {
  it('dice cómo entra y sale una sesión', () => {
    expect(formatModes('audio', ['audio', 'text'])).toBe('voz → voz y texto')
    expect(formatModes('text', ['text'])).toBe('texto → texto')
  })
  it('pone una cifra decimal con coma', () => {
    expect(formatSeconds(3200)).toBe('3,2 s')
    expect(formatSeconds(null)).toBe('—')
  })
})
