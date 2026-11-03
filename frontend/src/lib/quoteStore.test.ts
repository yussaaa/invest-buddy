import { afterEach, describe, expect, it, vi } from 'vitest'
import { clear } from './clientCache'
import { _inflightCount, fetchQuotes, readQuotes } from './quoteStore'
import type { Quote } from './types'

afterEach(() => {
  clear()
  vi.useRealTimers()
})

function recordingFetcher() {
  const requests: string[][] = []
  const fetcher = vi.fn(async (symbols: string[]) => {
    requests.push([...symbols])
    return { quotes: symbols.map(s => ({ symbol: s, last: 100 }) as Quote) }
  })
  return { fetcher, requests }
}

describe('quote store', () => {
  it('fetches each symbol once across overlapping sets', async () => {
    const { fetcher, requests } = recordingFetcher()
    await fetchQuotes(['AAPL', 'MSFT', 'NVDA'], { fetcher })   // the watchlist rail
    await fetchQuotes(['AAPL'], { fetcher })                   // the chart, a moment later
    expect(requests).toEqual([['AAPL', 'MSFT', 'NVDA']])
    expect(readQuotes(['aapl']).quotes.AAPL.last).toBe(100)
  })

  it('asks only for the symbols that are missing or stale', async () => {
    vi.useFakeTimers()
    const { fetcher, requests } = recordingFetcher()
    await fetchQuotes(['AAPL', 'MSFT'], { fetcher })
    vi.advanceTimersByTime(11_000)                              // past TTL.quotes
    await fetchQuotes(['MSFT'], { fetcher })
    await fetchQuotes(['AAPL', 'MSFT', 'TSLA'], { fetcher })
    expect(requests).toEqual([['AAPL', 'MSFT'], ['MSFT'], ['AAPL', 'TSLA']])
  })

  it('joins a request already in flight instead of racing it', async () => {
    let release!: () => void
    const gate = new Promise<void>(r => (release = r))
    const fetcher = vi.fn(async (symbols: string[]) => {
      await gate
      return { quotes: symbols.map(s => ({ symbol: s, last: 1 }) as Quote) }
    })
    const first = fetchQuotes(['AAPL', 'MSFT'], { fetcher })
    const second = fetchQuotes(['AAPL'], { fetcher })
    expect(fetcher).toHaveBeenCalledTimes(1)
    release()
    await Promise.all([first, second])
    expect(_inflightCount()).toBe(0)
  })

  it('force refetches even fresh symbols', async () => {
    const { fetcher, requests } = recordingFetcher()
    await fetchQuotes(['AAPL'], { fetcher })
    await fetchQuotes(['AAPL'], { fetcher, force: true })
    expect(requests).toHaveLength(2)
  })

  it('normalises symbols and ignores duplicates and blanks', async () => {
    const { fetcher, requests } = recordingFetcher()
    await fetchQuotes(['aapl', 'AAPL', ' msft ', ''], { fetcher })
    expect(requests).toEqual([['AAPL', 'MSFT']])
  })

  it('a failed request leaves nothing in flight and caches nothing', async () => {
    const fetcher = vi.fn(async () => {
      throw new Error('offline')
    })
    await expect(fetchQuotes(['AAPL'], { fetcher })).rejects.toThrow('offline')
    expect(_inflightCount()).toBe(0)
    expect(readQuotes(['AAPL']).quotes).toEqual({})
  })

  it('reports the newest production time across the set', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000)
    const { fetcher } = recordingFetcher()
    await fetchQuotes(['AAPL'], { fetcher })
    vi.setSystemTime(5_000)
    await fetchQuotes(['MSFT'], { fetcher })
    expect(readQuotes(['AAPL', 'MSFT']).updatedAt).toBe(5_000)
  })
})
