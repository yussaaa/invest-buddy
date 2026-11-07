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
  /** Per symbol, like the backend's `quote:{sym}` — see lib/quoteStore.ts. */
  quote: (symbol: string) => `quote:${symbol.toUpperCase()}`,
  history: (symbol: string, range: string) => `hist:${symbol}:${range}`,
  profile: (symbol: string) => `profile:${symbol}`,
  technicals: (symbol: string) => `technicals:${symbol}`,
  trend: (symbol: string, range: string) => `trend:${symbol}:${range}`,
  atr: (symbol: string) => `atr:${symbol}:14`,
  /** Assumption overrides are part of the key — they change the answer. */
  valuation: (symbol: string, params: Record<string, number | string> = {}) =>
    `valuation:${symbol}:${JSON.stringify(params)}`,
  /** Keyed on the same overrides as the valuation it describes. */
  valuationExplain: (symbol: string, params: Record<string, number | string> = {}) =>
    `valuation:explain:${symbol}:${JSON.stringify(params)}`,
  technicalsExplain: (symbol: string) => `technicals:explain:${symbol}`,
  optionStrategies: (symbol: string) => `options:strategies:${symbol}`,
  optionsExplain: (symbol: string, strategy: string) => `options:explain:${symbol}:${strategy}`,
  overview: () => 'overview',
  breadth: () => 'breadth',
  movers: (cap: CapTier, limit = 10) => `movers:${cap}:${limit}`,
  weekEvents: (offset: number, cap: CapTier) => `events:week:${cap}:${offset}`,
  signalSources: () => 'signals:sources',
  socialTrending: (source: string, filter: string, limit: number) =>
    `social:trending:${source}:${filter}:${limit}`,
  screenPresets: () => 'screen:presets',
  screen: (preset: string, limit: number) => `screen:${preset}:${limit}`,
  socialTicker: (symbol: string) => `social:snap:${symbol.toUpperCase()}`,
  socialExplain: (symbol: string) => `social:explain:${symbol.toUpperCase()}`,
  socialVol: (symbol: string) => `social:vol:${symbol.toUpperCase()}`,
  newsSources: () => 'news:sources',
  /** The first page only — the stream keeps it current after that. */
  newsFeed: () => 'news:feed',
} as const

/** Milliseconds. Server constants are in seconds — the names line up. */
export const TTL = {
  quotes: 10_000,             // market_data.QUOTES_TTL
  history: 30_000,            // market_data.HISTORY_TTL
  overview: 30_000,           // market_data.OVERVIEW_TTL
  movers: 60_000,             // market_data.MOVERS_TTL
  profile: 600_000,           // market_data.PROFILE_TTL
  technicals: 120_000,        // technicals.TECHNICALS_TTL
  trend: 300_000,             // technicals.TREND_TTL
  atr: 300_000,               // technicals.ATR_TTL
  valuation: 900_000,         // valuation.VALUATION_TTL
  explanation: 900_000,       // technicals/options EXPLANATION_TTL
  optionStrategies: 300_000,  // options.OPTIONS_CHAIN_TTL
  weekEvents: 1_800_000,      // market_data.EVENTS_TTL
  socialTrending: 300_000,    // social/apewisdom.PAGE_TTL
  screen: 120_000,            // screens.SCREEN_TTL
  socialTicker: 120_000,      // signals_page.SNAPSHOT_TTL
  socialExplain: 900_000,     // signals_page.EXPLANATION_TTL
  socialVol: 300_000,         // options.OPTIONS_CHAIN_TTL
  // The source list changes when a breaker trips or a key is added — rarely,
  // but a stale "blocked" chip for an hour would mislead.
  signalSources: 60_000,
  screenPresets: 86_400_000,  // a code constant on the server
  newsSources: 60_000,        // like signalSources: a chip can flip to blocked
  // Short: it is only the first paint. Live updates come from the stream.
  newsFeed: 30_000,

  // Deliberately NOT the server's 60s. The breadth scan prices ~700 symbols
  // and takes minutes, so a short client TTL buys nothing but another one of
  // them. See the poll interval in MarketPage, which matches.
  breadth: 600_000,
} as const
