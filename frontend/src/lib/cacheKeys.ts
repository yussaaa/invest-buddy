/**
 * Cache keys and TTLs for the client cache.
 *
 * The key strings deliberately match the ones `app/services/market_data.py`
 * uses for Redis, so the same read looks the same in the browser devtools and
 * in `redis-cli`. The TTLs mirror the server constants for the same reason;
 * where one deliberately diverges, the comment says why.
 */

import type { CapTier } from './types'

export const K = {
  quotes: (symbols: string[]) => `quotes:${symbols.join(',')}`,
  history: (symbol: string, range: string) => `hist:${symbol}:${range}`,
  profile: (symbol: string) => `profile:${symbol}`,
  technicals: (symbol: string) => `technicals:${symbol}`,
  technicalsExplain: (symbol: string) => `technicals:explain:${symbol}`,
  optionStrategies: (symbol: string) => `options:strategies:${symbol}`,
  optionsExplain: (symbol: string, strategy: string) => `options:explain:${symbol}:${strategy}`,
  overview: () => 'overview',
  breadth: () => 'breadth',
  movers: (cap: CapTier, limit = 10) => `movers:${cap}:${limit}`,
  weekEvents: (offset: number) => `events:week:${offset}`,
} as const

/** Milliseconds. Server constants are in seconds — the names line up. */
export const TTL = {
  quotes: 10_000,             // market_data.QUOTES_TTL
  history: 30_000,            // market_data.HISTORY_TTL
  overview: 30_000,           // market_data.OVERVIEW_TTL
  movers: 60_000,             // market_data.MOVERS_TTL
  profile: 600_000,           // market_data.PROFILE_TTL
  technicals: 120_000,        // technicals.TECHNICALS_TTL
  explanation: 900_000,       // technicals/options EXPLANATION_TTL
  optionStrategies: 300_000,  // options.OPTIONS_CHAIN_TTL
  weekEvents: 1_800_000,      // market_data.EVENTS_TTL

  // Deliberately NOT the server's 60s. The breadth scan prices ~700 symbols
  // and takes minutes, so a short client TTL buys nothing but another one of
  // them. See the poll interval in MarketPage, which matches.
  breadth: 600_000,
} as const
