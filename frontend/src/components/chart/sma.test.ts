import { describe, expect, it } from 'vitest'
import { sma } from './PriceChart'
import type { Candle } from '@/lib/types'

const candle = (i: number, close: number): Candle =>
  ({ time: `2026-01-${String(i + 1).padStart(2, '0')}`, open: close, high: close, low: close, close, volume: 0 })

const candles = [10, 11, 12, 13, 14].map((c, i) => candle(i, c))

describe('sma', () => {
  it('without a lead, starts `period` bars in', () => {
    const out = sma(candles, 3)
    expect(out.map(p => p.time)).toEqual(['2026-01-03', '2026-01-04', '2026-01-05'])
    expect(out[0].value).toBeCloseTo(11)
  })

  it('with a full lead, has a value on every candle', () => {
    const out = sma(candles, 3, [8, 9])
    expect(out).toHaveLength(candles.length)
    expect(out[0]).toEqual({ time: '2026-01-01', value: (8 + 9 + 10) / 3 })
    expect(out[4].value).toBeCloseTo((12 + 13 + 14) / 3)
  })

  it('never emits a point for a lead bar', () => {
    const out = sma(candles, 2, [1, 2, 3, 4])
    expect(out.map(p => p.time)).toEqual(candles.map(c => c.time))
  })

  it('a partial lead starts the line partway, at the right bar', () => {
    const out = sma(candles, 4, [9])
    expect(out[0].time).toBe('2026-01-03')
    expect(out[0].value).toBeCloseTo((9 + 10 + 11 + 12) / 4)
  })
})
