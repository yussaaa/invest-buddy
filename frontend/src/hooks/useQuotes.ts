/**
 * useQuotes — polls the backend for quotes on a set of symbols.
 * One batched request per tick; the backend caches upstream calls.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { Quote } from '../lib/types'

export function useQuotes(symbols: string[], intervalMs = 15000) {
  const [quotes, setQuotes] = useState<Record<string, Quote>>({})
  const [loading, setLoading] = useState(false)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Stable key so the effect only re-runs when the symbol set actually changes.
  const key = symbols.join(',')
  const abortRef = useRef<AbortController | null>(null)

  const refresh = useCallback(async () => {
    const list = key ? key.split(',') : []
    if (list.length === 0) {
      setQuotes({})
      return
    }
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    try {
      const { quotes: fetched } = await api.market.quotes(list, controller.signal)
      setQuotes(prev => {
        const next = { ...prev }
        for (const q of fetched) next[q.symbol] = q
        return next
      })
      setUpdatedAt(Date.now())
      setError(null)
    } catch (e) {
      if (controller.signal.aborted) return
      setError(e instanceof Error ? e.message : 'Failed to load quotes')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }, [key])

  useEffect(() => {
    refresh()
    if (!key) return
    const id = setInterval(refresh, intervalMs)
    return () => {
      clearInterval(id)
      abortRef.current?.abort()
    }
  }, [refresh, key, intervalMs])

  return { quotes, loading, updatedAt, error, refresh }
}
