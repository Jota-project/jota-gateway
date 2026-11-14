import { describe, expect, it } from 'vitest'
import { formatClock, formatDuration, formatModes, formatRelative, formatSeconds, formatShortDate } from './format'

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

describe('formatRelative, formatClock y formatShortDate', () => {
  const now = new Date(2026, 10, 14, 18, 30) // sábado 14 nov 2026, 18:30 (hora local)
  const at = (d: number, h: number, m = 0) => new Date(2026, 10, d, h, m).toISOString()

  it('dice cuándo fue, de más cerca a más lejos', () => {
    expect(formatRelative(new Date(now.getTime() - 30_000).toISOString(), now)).toBe('ahora mismo')
    expect(formatRelative(at(14, 18, 29), now)).toBe('hace 1 min')
    expect(formatRelative(at(14, 18, 18), now)).toBe('hace 12 min')
    expect(formatRelative(at(14, 9, 5), now)).toBe('hoy, 09:05')
    expect(formatRelative(at(13, 22, 10), now)).toBe('ayer, 22:10')
    expect(formatRelative(at(11, 12), now)).toBe('hace 3 días')
    expect(formatRelative(at(2, 12), now)).toBe('2 nov')
  })

  it('pone el año solo si no es este', () => {
    expect(formatShortDate('2026-11-14', now)).toBe('14 nov')
    expect(formatShortDate('2025-03-01', now)).toBe('1 mar 2025')
  })

  it('la hora sola si es de hoy; si no, con el día', () => {
    expect(formatClock(at(14, 8, 7), now)).toBe('08:07')
    expect(formatClock(at(12, 20, 0), now)).toBe('12 nov, 20:00')
  })
})
