import { describe, expect, it } from 'vitest'
import { formatDuration } from './format'

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
