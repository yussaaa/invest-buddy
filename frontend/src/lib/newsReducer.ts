/**
 * Merging the news wire: the first page, then whatever the stream sends.
 *
 * Pure, so the rules are tested rather than eyeballed:
 * - one row per id — a later stage (triage, explanation) restamps a row and
 *   the stream sends it again, which must replace rather than duplicate it;
 * - a stale copy never overwrites a fresher one (a slow first page landing
 *   after the stream has already delivered newer stamps);
 * - newest *published* first, capped, so a page left open all day stays light.
 */

import type { NewsCategory, NewsHeadline, NewsSourceId } from './types'

export const FEED_CAP = 300

export function mergeHeadlines(
  current: NewsHeadline[],
  incoming: NewsHeadline[],
  cap = FEED_CAP,
): NewsHeadline[] {
  if (!incoming.length) return current
  const byId = new Map(current.map(h => [h.id, h]))
  for (const h of incoming) {
    const existing = byId.get(h.id)
    if (!existing || h.updated_at >= existing.updated_at) byId.set(h.id, h)
  }
  return [...byId.values()]
    .sort((a, b) => (a.published_at < b.published_at ? 1 : a.published_at > b.published_at ? -1 : a.id < b.id ? -1 : 1))
    .slice(0, cap)
}

/** The newest stamp held — what a reconnect passes as `since`. */
export function newestStamp(headlines: NewsHeadline[]): string | null {
  let newest: string | null = null
  for (const h of headlines) if (newest === null || h.updated_at > newest) newest = h.updated_at
  return newest
}

export interface NewsFilter {
  sources: NewsSourceId[]          // empty = all
  categories: NewsCategory[]       // empty = all
  /** Only headlines naming one of these; null = no ticker filter. */
  symbols: string[] | null
  query: string
}

export const EMPTY_FILTER: NewsFilter = { sources: [], categories: [], symbols: null, query: '' }

export function filterHeadlines(headlines: NewsHeadline[], f: NewsFilter): NewsHeadline[] {
  const sources = new Set(f.sources)
  const categories = new Set(f.categories)
  const symbols = f.symbols ? new Set(f.symbols.map(s => s.toUpperCase())) : null
  const query = f.query.trim().toLowerCase()
  return headlines.filter(h => {
    if (sources.size && !sources.has(h.source)) return false
    if (categories.size && !(h.category && categories.has(h.category))) return false
    if (symbols && !h.symbols.some(s => symbols.has(s))) return false
    if (query) {
      const haystack = `${h.title} ${h.summary ?? ''} ${h.symbols.join(' ')}`.toLowerCase()
      if (!haystack.includes(query)) return false
    }
    return true
  })
}
