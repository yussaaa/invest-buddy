/**
 * Quotes, cached per symbol rather than per request.
 *
 * Keyed by the whole symbol set, the chart's `['AAPL']` and the watchlist
 * rail's fifty tickers were different cache entries, so each polled on its own
 * timer and AAPL was fetched twice every tick. This mirrors the backend's
 * `get_fresh_many` / `put_many` (services/market_data.py:get_quotes): find the
 * symbols that are not fresh, fetch only those in one batch, and store each
 * quote under its own key. Overlapping sets then share whatever is fresh, and
 * a poll whose symbols were all just fetched by someone else sends nothing.
 */

import { api } from './api'
import { K, TTL } from './cacheKeys'
import { isFresh, peek, put } from './clientCache'
import type { Quote } from './types'

type Fetcher = (symbols: string[]) => Promise<{ quotes: Quote[] }>

// One promise per symbol currently being fetched, so two polls that overlap
// in time join the same request instead of racing.
const inflight = new Map<string, Promise<void>>()

export function normalise(symbols: string[]): string[] {
  return [...new Set(symbols.map(s => s.trim().toUpperCase()).filter(Boolean))]
}

export async function fetchQuotes(
  symbols: string[],
  { force = false, fetcher = api.market.quotes as Fetcher }: { force?: boolean; fetcher?: Fetcher } = {},
): Promise<void> {
  const waiting: Promise<void>[] = []
  const missing: string[] = []

  for (const symbol of normalise(symbols)) {
    const pending = inflight.get(symbol)
    if (pending) waiting.push(pending)
    else if (force || !isFresh(K.quote(symbol), TTL.quotes)) missing.push(symbol)
  }

  if (missing.length) {
    const request = fetcher(missing)
      .then(({ quotes }) => {
        // Errored quotes are stored too, as the per-set cache did: "no data"
        // is an answer, and re-asking for it every tick helps nobody.
        for (const quote of quotes) put(K.quote(quote.symbol), quote)
      })
      .finally(() => {
        for (const symbol of missing) {
          if (inflight.get(symbol) === request) inflight.delete(symbol)
        }
      })
    for (const symbol of missing) inflight.set(symbol, request)
    waiting.push(request)
  }

  await Promise.all(waiting)
}

/** Whatever is cached for these symbols, at any age. */
export function readQuotes(symbols: string[]): { quotes: Record<string, Quote>; updatedAt: number } {
  const quotes: Record<string, Quote> = {}
  let updatedAt = 0
  for (const symbol of normalise(symbols)) {
    const snap = peek<Quote>(K.quote(symbol))
    if (snap.value !== undefined) {
      quotes[symbol] = snap.value
      updatedAt = Math.max(updatedAt, snap.producedAt)
    }
  }
  return { quotes, updatedAt }
}

/** Test seam. */
export function _inflightCount(): number {
  return inflight.size
}
