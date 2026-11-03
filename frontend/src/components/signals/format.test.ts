import { describe, expect, it } from 'vitest'
import { attentionLabel, compact, crowdSplit, rankDelta, relVolume, signedPct, splitCitations } from './format'

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
    expect(attentionLabel(null, 3).text).toBe('3/7d')
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
