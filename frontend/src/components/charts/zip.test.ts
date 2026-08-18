import { describe, expect, it } from 'vitest'
import { zipTrend } from './zip'
import type { TrendSeries } from '@/lib/types'

function series(over: Partial<TrendSeries> = {}): TrendSeries {
  return {
    dates: ['2026-01-01', '2026-01-02', '2026-01-03'],
    price: [10, 11, 12],
    sma: { '20': [1, 2, 3], '50': [4, 5, 6], '200': [7, 8, 9] },
    band_upper: [20, 21, 22],
    band_lower: [5, 6, 7],
    slope_per_day: { '20': [0.1, 0.2, 0.3], '50': [0.4, 0.5, 0.6], '200': [0.7, 0.8, 0.9] },
    z_score: [-1, 0, 1],
    ...over,
  }
}

describe('zipTrend', () => {
  it('turns columns into one row per date', () => {
    const rows = zipTrend(series())
    expect(rows).toHaveLength(3)
    expect(rows[1]).toEqual({
      date: '2026-01-02', price: 11,
      sma20: 2, sma50: 5, sma200: 8,
      band: [6, 21],
      slope20: 0.2, slope50: 0.5, slope200: 0.8,
      z: 0,
    })
  })

  it('returns nothing for an absent or empty series', () => {
    expect(zipTrend(undefined)).toEqual([])
    expect(zipTrend(series({ dates: [] }))).toEqual([])
  })

  it('carries nulls through rather than coercing them to zero', () => {
    // A zero on a slope chart reads as "flat"; a null draws no point at all.
    const rows = zipTrend(series({ z_score: [null, null, 1] }))
    expect(rows[0].z).toBeNull()
    expect(rows[2].z).toBe(1)
  })

  it('drops the band when either endpoint is missing', () => {
    const rows = zipTrend(series({ band_lower: [null, 6, 7] }))
    expect(rows[0].band).toBeNull()
    expect(rows[1].band).toEqual([6, 21])
  })

  it('stops at the shortest column rather than reading past the end', () => {
    const rows = zipTrend(series({ price: [10] }))
    expect(rows).toHaveLength(1)
  })

  it('survives a missing SMA window', () => {
    const rows = zipTrend(series({ sma: { '20': [1, 2, 3] } }))
    expect(rows[0].sma20).toBe(1)
    expect(rows[0].sma200).toBeNull()
  })
})
