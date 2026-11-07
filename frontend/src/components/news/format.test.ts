import { describe, expect, it } from 'vitest'
import { ago, etDayKey, etDayLabel, etTime } from './format'

describe('news time formatting', () => {
  it('reads times on the New York clock', () => {
    expect(etTime('2026-11-06T13:31:02Z')).toBe('08:31')          // EST, UTC-5
    expect(etTime('2026-07-06T13:31:02Z')).toBe('09:31')          // EDT, UTC-4
  })

  it('keeps an evening headline on its New York date, not the UTC one', () => {
    // 01:30 UTC on the 7th is 20:30 on the 6th in New York.
    expect(etDayKey('2026-11-07T01:30:00Z')).toBe('2026-11-06')
  })

  it('labels today and yesterday relative to New York', () => {
    const now = new Date('2026-11-06T18:00:00Z')
    expect(etDayLabel('2026-11-06T14:00:00Z', now)).toBe('Today')
    expect(etDayLabel('2026-11-07T01:30:00Z', now)).toBe('Today')
    expect(etDayLabel('2026-11-05T14:00:00Z', now)).toBe('Yesterday')
    expect(etDayLabel('2026-11-02T14:00:00Z', now)).toBe('Mon, Nov 2')
  })

  it('says how long ago', () => {
    const now = new Date('2026-11-06T18:00:00Z')
    expect(ago('2026-11-06T17:59:30Z', now)).toBe('just now')
    expect(ago('2026-11-06T17:56:00Z', now)).toBe('4m ago')
    expect(ago('2026-11-06T15:00:00Z', now)).toBe('3h ago')
    expect(ago('2026-11-04T17:00:00Z', now)).toBe('2d ago')
  })
})
