import { describe, expect, it } from 'vitest'
import type { NewsHeadline } from './types'
import { EMPTY_FILTER, filterHeadlines, mergeHeadlines, newestStamp } from './newsReducer'

function h(id: string, published: string, stamp = published, extra: Partial<NewsHeadline> = {}): NewsHeadline {
  return {
    id, source: 'alpaca', publisher: 'Benzinga', title: `headline ${id}`, summary: null, url: null,
    published_at: published, symbols: [], category: 'news', tags: [], importance: null,
    exposures: null, explanation: null, reaction: null, updated_at: stamp, ...extra,
  }
}

const T = (m: number) => `2026-11-06T13:${String(m).padStart(2, '0')}:00+00:00`

describe('mergeHeadlines', () => {
  it('orders by publication, newest first', () => {
    const merged = mergeHeadlines([h('a', T(1))], [h('c', T(3)), h('b', T(2))])
    expect(merged.map(x => x.id)).toEqual(['c', 'b', 'a'])
  })

  it('replaces a row a later stage restamped, without duplicating it', () => {
    const merged = mergeHeadlines([h('a', T(1))], [h('a', T(1), T(5), { importance: 4 })])
    expect(merged).toHaveLength(1)
    expect(merged[0].importance).toBe(4)
  })

  it('never lets a stale copy overwrite a fresher one', () => {
    const fresh = h('a', T(1), T(5), { importance: 4 })
    const merged = mergeHeadlines([fresh], [h('a', T(1), T(1))])
    expect(merged[0].importance).toBe(4)
  })

  it('caps the feed, dropping the oldest', () => {
    const many = Array.from({ length: 10 }, (_, i) => h(`n${i}`, T(i)))
    expect(mergeHeadlines([], many, 3).map(x => x.id)).toEqual(['n9', 'n8', 'n7'])
  })

  it('returns the same array when nothing arrives', () => {
    const current = [h('a', T(1))]
    expect(mergeHeadlines(current, [])).toBe(current)
  })
})

describe('newestStamp', () => {
  it('is the newest updated_at, not the newest publication', () => {
    expect(newestStamp([h('a', T(9), T(9)), h('b', T(1), T(30))])).toBe(T(30))
    expect(newestStamp([])).toBeNull()
  })
})

describe('filterHeadlines', () => {
  const rows = [
    h('nvda', T(3), T(3), { symbols: ['NVDA'], category: 'news' }),
    h('fomc', T(2), T(2), { source: 'fed', category: 'monetary_policy', title: 'FOMC statement' }),
    h('tg', T(1), T(1), { source: 'edgar', symbols: ['TG'], category: 'earnings' }),
  ]
  const ids = (f = EMPTY_FILTER) => filterHeadlines(rows, f).map(x => x.id)

  it('passes everything with an empty filter', () => expect(ids()).toEqual(['nvda', 'fomc', 'tg']))
  it('filters by source and category', () => {
    expect(ids({ ...EMPTY_FILTER, sources: ['fed', 'edgar'] })).toEqual(['fomc', 'tg'])
    expect(ids({ ...EMPTY_FILTER, categories: ['earnings'] })).toEqual(['tg'])
  })
  it('keeps only watchlist names when given symbols, case-insensitively', () => {
    expect(ids({ ...EMPTY_FILTER, symbols: ['nvda'] })).toEqual(['nvda'])
    expect(ids({ ...EMPTY_FILTER, symbols: [] })).toEqual([])
  })
  it('searches title and tickers', () => {
    expect(ids({ ...EMPTY_FILTER, query: 'fomc' })).toEqual(['fomc'])
    expect(ids({ ...EMPTY_FILTER, query: 'tg' })).toEqual(['tg'])
  })
})
