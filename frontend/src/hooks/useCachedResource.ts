/**
 * React bindings for the client cache.
 *
 * The one thing that matters here: `useSyncExternalStore` reads the store
 * during the *first* render. There is no "effect fires, state updates, second
 * render" gap, which is what would otherwise flash a spinner for one frame on
 * data that was already in hand. That synchronous read is the whole reason
 * navigating back to a page feels instant rather than merely fast.
 */

import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react'
import {
  cached,
  emptySnapshot,
  peek,
  revalidate,
  subscribe,
  type Snapshot,
} from '@/lib/clientCache'

export interface UseCachedResourceOptions<T> {
  ttl: number
  /** Omit for a one-shot read. */
  refreshIntervalMs?: number
  /** False parks the resource without fetching — for a key that isn't ready. */
  enabled?: boolean
  shouldCache?: (value: T) => boolean
  /**
   * Hold the previous key's value while a new key loads.
   *
   * Off by default, and that is a correctness decision rather than a default
   * chosen at random: showing last week's earnings under a "this week" heading,
   * or mid-cap movers under an "all caps" label, is worse than showing nothing.
   * Only turn it on where the surrounding labels don't change with the key.
   */
  keepPreviousData?: boolean
}

export interface CachedResource<T> {
  data: T | undefined
  error: Error | undefined
  /** Nothing to show yet — this is the one that gates a spinner. */
  isLoading: boolean
  /** A request is in flight; there may already be data on screen. */
  isValidating: boolean
  /** Epoch ms the shown value was fetched. 0 if never. */
  updatedAt: number
  refresh: () => Promise<void>
}

export function useCachedResource<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  options: UseCachedResourceOptions<T>,
): CachedResource<T> {
  const { ttl, refreshIntervalMs, enabled = true, shouldCache, keepPreviousData } = options

  // Callers pass an inline arrow, which is a new function every render. Behind
  // a ref, the effects below can depend on `key` alone.
  const fetcherRef = useRef(fetcher)
  useEffect(() => {
    fetcherRef.current = fetcher
  })

  const shouldCacheRef = useRef(shouldCache)
  useEffect(() => {
    shouldCacheRef.current = shouldCache
  })

  const snapshot = useSyncExternalStore(
    useCallback((onChange: () => void) => (key ? subscribe(key, onChange) : () => {}), [key]),
    useCallback(() => (key ? peek<T>(key) : emptySnapshot<T>()), [key]),
  )

  const load = useCallback(() => {
    if (!key || !enabled) return
    void cached(key, () => fetcherRef.current(), {
      ttl,
      shouldCache: shouldCacheRef.current,
    }).catch(() => {
      /* surfaced through snapshot.error */
    })
  }, [key, enabled, ttl])

  // Mount and key changes. `cached` is a no-op inside the TTL, so StrictMode's
  // double-invoke in dev costs one function call, not one request.
  useEffect(load, [load])

  useEffect(() => {
    if (!key || !enabled || !refreshIntervalMs) return
    const id = setInterval(() => {
      if (document.hidden) return  // a backgrounded tab has nobody watching
      load()
    }, refreshIntervalMs)
    return () => clearInterval(id)
  }, [key, enabled, refreshIntervalMs, load])

  const refresh = useCallback(async () => {
    if (!key) return
    try {
      await revalidate(key, () => fetcherRef.current(), {
        ttl,
        shouldCache: shouldCacheRef.current,
      })
    } catch {
      /* surfaced through snapshot.error */
    }
  }, [key, ttl])

  const previous = useRef<T | undefined>(undefined)
  if (snapshot.value !== undefined) previous.current = snapshot.value
  const data = snapshot.value ?? (keepPreviousData ? previous.current : undefined)

  return {
    data,
    error: snapshot.error,
    // Not `&& isFetching`: between the first render and the effect firing,
    // nothing is in flight yet, and gating on it would blink an empty state.
    isLoading: data === undefined && snapshot.error === undefined,
    isValidating: snapshot.isFetching,
    updatedAt: snapshot.producedAt,
    refresh,
  }
}

/**
 * Subscribe to a key without ever fetching it.
 *
 * For values that are produced on demand rather than on mount — the LLM
 * explanations behind the "explain this" buttons. Reading them from the cache
 * instead of local state is what stops a round trip being thrown away every
 * time you change page.
 */
export function useCachedValue<T>(key: string | null): Snapshot<T> {
  return useSyncExternalStore(
    useCallback((onChange: () => void) => (key ? subscribe(key, onChange) : () => {}), [key]),
    useCallback(() => (key ? peek<T>(key) : emptySnapshot<T>()), [key]),
  )
}
