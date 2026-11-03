import { describe, expect, it } from 'vitest'
import { ASYMMETRY_NOTE_RATIO, bandAsymmetry } from './band'

describe('bandAsymmetry', () => {
  it('measures the JNJ-shaped band the roadmap describes as lopsided', () => {
    // $126 / $246 / $799 — symmetric inputs, convex output.
    const a = bandAsymmetry([
      { case: 'bear', value_per_share: 126 },
      { case: 'base', value_per_share: 246 },
      { case: 'bull', value_per_share: 799 },
    ])!
    expect(a.upside).toBeCloseTo(799 / 246 - 1, 6)
    expect(a.downside).toBeCloseTo(1 - 126 / 246, 6)
    expect(a.ratio).toBeGreaterThan(ASYMMETRY_NOTE_RATIO)
  })

  it('stays quiet on a near-symmetric band', () => {
    const a = bandAsymmetry([
      { case: 'bear', value_per_share: 80 },
      { case: 'base', value_per_share: 100 },
      { case: 'bull', value_per_share: 125 },
    ])!
    expect(a.ratio).toBeLessThanOrEqual(ASYMMETRY_NOTE_RATIO)
  })

  it('returns null when any case failed or the band is inverted', () => {
    expect(bandAsymmetry([])).toBeNull()
    expect(bandAsymmetry([
      { case: 'bear', error: 'spread too small' },
      { case: 'base', value_per_share: 100 },
      { case: 'bull', value_per_share: 150 },
    ])).toBeNull()
    expect(bandAsymmetry([
      { case: 'bear', value_per_share: 120 },
      { case: 'base', value_per_share: 100 },
      { case: 'bull', value_per_share: 150 },
    ])).toBeNull()
  })
})
