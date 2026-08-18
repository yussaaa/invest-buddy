/**
 * useQuotes — polls the backend for quotes on a set of symbols.
 *
 * One batched request per tick, over the client cache. The cache is what makes
 * the watchlist rail keep its prices when you change page, and what stops two
 * components watching overlapping symbol sets from each running their own
 * timer against the same key.
 */

import { useMemo } from 'react'
import { api } from '../lib/api'
import { K, TTL } from '../lib/cacheKeys'
import { useCachedResource } from './useCachedResource'
import type { Quote } from '../lib/types'

export function useQuotes(symbols: string[], intervalMs = 15000) {
  // Stable key so the resource only changes when the symbol set really does.
  const joined = symbols.join(',')

  const res = useCachedResource(
    joined ? K.quotes(symbols) : null,
    () => api.market.quotes(joined.split(',')),
    {
      ttl: TTL.quotes,
      refreshIntervalMs: intervalMs,
      // Adding a ticker to a watchlist changes the key. Holding the previous
      // response means the rail keeps showing prices instead of blanking while
      // the wider set loads — the reason the old hook merged into prior state.
      keepPreviousData: true,
    },
  )

  const quotes = useMemo(() => {
    const map: Record<string, Quote> = {}
    for (const q of res.data?.quotes ?? []) map[q.symbol] = q
    return map
  }, [res.data])

  return {
    quotes,
    loading: res.isLoading,
    /** 0 until the first response — falsy, as the old `null` was. */
    updatedAt: res.updatedAt,
    error: res.error?.message ?? null,
    refresh: res.refresh,
  }
}
