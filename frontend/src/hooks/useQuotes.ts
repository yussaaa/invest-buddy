/**
 * useQuotes — polls the backend for quotes on a set of symbols.
 *
 * Quotes are cached per symbol (lib/quoteStore.ts), so components watching
 * overlapping sets share them: the chart's ticker is fetched once per tick
 * even while the watchlist rail polls fifty others alongside it. The cache is
 * also what keeps the rail's prices when you change page, and what stops a
 * newly added ticker from blanking the rest while it loads.
 */

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { K } from '../lib/cacheKeys'
import { peek, subscribe } from '../lib/clientCache'
import { fetchQuotes, normalise, readQuotes } from '../lib/quoteStore'
import { usePageActive } from '../context/PageActive'

export function useQuotes(symbols: string[], intervalMs = 15000) {
  const joined = normalise(symbols).join(',')
  const wanted = useMemo(() => (joined ? joined.split(',') : []), [joined])
  const [error, setError] = useState<string | null>(null)

  const active = usePageActive()
  const activeRef = useRef(active)
  activeRef.current = active

  // Re-render when any watched symbol's entry changes. The snapshot is a
  // string of production times — compared by value, so it is stable between
  // changes the way useSyncExternalStore requires.
  const version = useSyncExternalStore(
    useCallback(
      (onChange: () => void) => {
        const unsubscribes = wanted.map(s => subscribe(K.quote(s), onChange))
        return () => unsubscribes.forEach(u => u())
      },
      [wanted],
    ),
    useCallback(() => wanted.map(s => peek(K.quote(s)).producedAt).join(','), [wanted]),
  )

  const load = useCallback(
    async (force = false) => {
      if (!wanted.length) return
      try {
        await fetchQuotes(wanted, { force })
        setError(null)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    },
    [wanted],
  )

  useEffect(() => {
    if (active) void load()
  }, [load, active])

  useEffect(() => {
    if (!wanted.length || !intervalMs) return
    const id = setInterval(() => {
      if (document.hidden || !activeRef.current) return
      void load()
    }, intervalMs)
    return () => clearInterval(id)
  }, [wanted, intervalMs, load])

  // `version` is not read inside, but it is what changes when a quote lands.
  const { quotes, updatedAt } = useMemo(() => readQuotes(wanted), [wanted, version])

  return {
    quotes,
    loading: Object.keys(quotes).length === 0 && error === null && wanted.length > 0,
    /** 0 until the first response — falsy, as the old `null` was. */
    updatedAt,
    error,
    refresh: useCallback(() => load(true), [load]),
  }
}
