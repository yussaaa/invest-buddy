import { describe, expect, it } from 'vitest'
import {
  attentionLabel, capTier, compact, crowdSplit, rankDelta, relVolume, signedPct, splitCitations, yearChangeBand,
} from './format'

describe('capTier', () => {
  it('buckets on the Market page boundaries, inclusive at the floor', () => {
    expect(capTier(4.99e12)?.id).toBe('mega')
    expect(capTier(200e9)?.id).toBe('mega')
    expect(capTier(199e9)?.id).toBe('large')
    expect(capTier(10e9)?.id).toBe('large')
    expect(capTier(3.7e9)?.id).toBe('mid')
    expect(capTier(300e6)?.id).toBe('small')
    expect(capTier(120e6)?.id).toBe('micro')
  })
  it('has no tier for a missing cap', () => {
    expect(capTier(null)).toBeNull()
    expect(capTier(0)).toBeNull()
  })
})

describe('yearChangeBand', () => {
  it('tints harder the bigger the move, either way', () => {
    expect(yearChangeBand(150)?.label).toBe('+100% or more')
    expect(yearChangeBand(62)?.label).toBe('+25% to +100%')
    expect(yearChangeBand(2)?.label).toBe('0 to +25%')
    expect(yearChangeBand(-7)?.label).toBe('0 to −25%')
    expect(yearChangeBand(-27)?.label).toBe('−25% to −50%')
    expect(yearChangeBand(-70)?.label).toBe('−50% or worse')
  })
  it('has no band for a missing value', () => {
    expect(yearChangeBand(null)).toBeNull()
  })
})

describe('rankDelta', () => {
  it('reads positive as a climb', () => {
    expect(rankDelta(8)).toEqual({ text: '▲8', tone: 'up' })
    expect(rankDelta(-3)).toEqual({ text: '▼3', tone: 'down' })
    expect(rankDelta(0).tone).toBe('flat')
  })
  it('marks a new entry rather than inventing a change', () => {
    expect(rankDelta(null).tone).toBe('new')
  })
})

describe('attentionLabel', () => {
  it('shows a z-score once history exists, emphasised at 2σ', () => {
    expect(attentionLabel(2.04, 14)).toMatchObject({ text: '+2.0σ', emphasis: true })
    expect(attentionLabel(1.9, 14).emphasis).toBe(false)
  })
  it('shows progress toward a baseline before then', () => {
    expect(attentionLabel(null, 3).text).toBe('day 3 of 7')
    expect(attentionLabel(null, 0).text).toBe('—')
  })
})

describe('numbers', () => {
  it('formats signed percents with a real minus', () => {
    expect(signedPct(12.4)).toBe('+12%')
    expect(signedPct(-3.25, 1)).toBe('−3.3%')
    expect(signedPct(null)).toBe('—')
  })
  it('compacts large counts', () => {
    expect(compact(325960)).toBe('326.0K')
    expect(compact(2_500_000_000)).toBe('2.5B')
  })
  it('only emphasises unusual relative volume', () => {
    expect(relVolume(2.6)).toEqual({ text: '2.6×', emphasis: true })
    expect(relVolume(1.1).emphasis).toBe(false)
  })
  it('splits the crowd including untagged messages', () => {
    expect(crowdSplit(6, 2, 2)).toEqual({ bull: 60, bear: 20, none: 20 })
    expect(crowdSplit(0, 0, 0)).toEqual({ bull: 0, bear: 0, none: 0 })
  })
})

describe('splitCitations', () => {
  it('turns known markers into links and drops unknown ones', () => {
    const parts = splitCitations('Contract won [S2][S9].', new Set(['S2']))
    expect(parts).toEqual(['Contract won ', { id: 'S2' }, '.'])
  })
})
