/**
 * A TTL cache for fetched data, living above the router.
 *
 * The problem it solves is not latency — the API answers most reads in single
 * -digit milliseconds. It is that react-router unmounts a page on navigation,
 * so every `useState` holding a response is thrown away and every effect
 * re-runs. The data was already here; nothing was keeping it.
 *
 * This is deliberately the same shape as `backend/app/services/cache.py` —
 * TTL, single-flight, serve-last-good, and a `shouldCache` guard — so the two
 * tiers read the same way when you are chasing a stale value through both.
 *
 * ── On aborts ─────────────────────────────────────────────────────────────
 * Nothing here ever takes an AbortSignal, and unmounting never cancels a
 * request. That is the point, not an omission:
 *
 *   - A request is shared. One component unmounting must not cancel the fetch
 *     another is waiting on, and a cancelled flight would otherwise reject the
 *     shared promise and leave an error on a key that has perfectly good data.
 *   - Letting it finish is what warms the cache for the trip back, which is
 *     the entire reason this module exists.
 *   - Single-flight already does the job aborting was doing — a second caller
 *     joins the first rather than starting another. It also dedupes across
 *     components, which aborting never did.
 *
 * The race that AbortController was guarding against — a slow response for the
 * old key landing after the new one — cannot happen here, because a response
 * is stored under the key it was requested for. A component reads the snapshot
 * for the key it currently wants; a late arrival for a key it has moved on
 * from lands somewhere it is no longer looking.
 */

/**
 * What a subscriber sees.
 *
 * Referentially stable between mutations, which `useSyncExternalStore`
 * requires — it throws if `getSnapshot` hands back a fresh object each call.
 * `commit()` is the only place a Snapshot is constructed.
 */
export interface Snapshot<T> {
  value: T | undefined
  /** Epoch ms the fetch *started*, matching the backend. 0 if never resolved. */
  producedAt: number
  /** Last failure since the last success. Never clears `value`. */
  error: Error | undefined
  isFetching: boolean
}

export interface CacheOptions<T> {
  ttl: number
  /**
   * Refuse to store a partial response. The provider drops symbols from a
   * batch now and then, and pinning that for a whole TTL shows gaps instead of
   * self-healing on the next read. The backend guards the same way.
   */
  shouldCache?: (value: T) => boolean
}

interface Entry<T> {
  snapshot: Snapshot<T>
  inflight: Promise<T> | null
  subs: Set<() => void>
}

/**
 * Bounded, unlike the backend's process cache: a MAX-range history is hundreds
 * of KB and the user can type any ticker they like. Insertion-ordered Map plus
 * a delete/re-set on touch gives an approximate LRU.
 */
const MAX_ENTRIES = 120

const store = new Map<string, Entry<unknown>>()

const EMPTY: Snapshot<never> = {
  value: undefined,
  producedAt: 0,
  error: undefined,
  isFetching: false,
}

/** The stable empty snapshot, for a null key or a key never seen. */
export function emptySnapshot<T>(): Snapshot<T> {
  return EMPTY as Snapshot<T>
}

function asError(e: unknown): Error {
  return e instanceof Error ? e : new Error(String(e))
}

function entryFor<T>(key: string): Entry<T> {
  let entry = store.get(key) as Entry<T> | undefined
  if (entry) {
    // Touch for LRU ordering.
    store.delete(key)
    store.set(key, entry as Entry<unknown>)
    return entry
  }
  entry = { snapshot: EMPTY as Snapshot<T>, inflight: null, subs: new Set() }
  store.set(key, entry as Entry<unknown>)
  evict()
  return entry
}

/** Drop the coldest entries. Anything live is untouchable. */
function evict(): void {
  if (store.size <= MAX_ENTRIES) return
  for (const [key, entry] of store) {
    if (store.size <= MAX_ENTRIES) return
    // Evicting a key a mounted component is reading would pull the data out
    // from under it; an in-flight fetch would lose its destination.
    if (entry.subs.size > 0 || entry.inflight) continue
    store.delete(key)
  }
}

function commit<T>(key: string, patch: Partial<Snapshot<T>>): void {
  const entry = entryFor<T>(key)
  entry.snapshot = { ...entry.snapshot, ...patch }
  // Copy first: a subscriber may unsubscribe from inside its own callback.
  for (const notify of [...entry.subs]) notify()
}

function begin<T>(
  key: string,
  fetcher: () => Promise<T>,
  shouldCache?: (value: T) => boolean,
): Promise<T> {
  const entry = entryFor<T>(key)
  const startedAt = Date.now()

  const promise = fetcher().then(
    value => {
      if (entry.inflight !== promise) return value  // superseded by a forced refresh
      entry.inflight = null
      if (!shouldCache || shouldCache(value)) {
        commit<T>(key, { value, producedAt: startedAt, error: undefined, isFetching: false })
      } else {
        commit<T>(key, { isFetching: false })
      }
      return value
    },
    err => {
      if (entry.inflight === promise) {
        entry.inflight = null
        // The last good value survives — this is the backend's serve-stale,
        // and it is why a flaky poll never blanks a populated panel.
        commit<T>(key, { error: asError(err), isFetching: false })
      }
      throw err
    },
  )

  entry.inflight = promise
  commit<T>(key, { isFetching: true })

  // Callers are allowed to drop the promise — the hook fires and forgets, and
  // reads the outcome off the snapshot. Without this, every network blip is an
  // unhandled rejection in the console.
  promise.catch(() => {})

  return promise
}

/** Fresh-or-fetch. Resolves from cache inside the TTL, joins any flight, else starts one. */
export function cached<T>(
  key: string,
  fetcher: () => Promise<T>,
  { ttl, shouldCache }: CacheOptions<T>,
): Promise<T> {
  const entry = entryFor<T>(key)

  if (entry.snapshot.value !== undefined && Date.now() - entry.snapshot.producedAt < ttl) {
    return Promise.resolve(entry.snapshot.value)
  }
  if (entry.inflight) return entry.inflight
  return begin(key, fetcher, shouldCache)
}

/** Ignore the TTL, but still single-flight — a double-clicked refresh fetches once. */
export function revalidate<T>(
  key: string,
  fetcher: () => Promise<T>,
  { shouldCache }: CacheOptions<T>,
): Promise<T> {
  const entry = entryFor<T>(key)
  if (entry.inflight) return entry.inflight
  return begin(key, fetcher, shouldCache)
}

/** Synchronous read at any age — the analogue of the backend's `peek`. */
export function peek<T>(key: string): Snapshot<T> {
  return (store.get(key)?.snapshot as Snapshot<T> | undefined) ?? emptySnapshot<T>()
}

/** Synchronous write, for a value produced outside a fetcher. */
export function put<T>(key: string, value: T): void {
  commit<T>(key, { value, producedAt: Date.now(), error: undefined, isFetching: false })
}

export function isFresh(key: string, ttl: number): boolean {
  const snapshot = store.get(key)?.snapshot
  return snapshot?.value !== undefined && Date.now() - snapshot.producedAt < ttl
}

/** Exact key, or every key under a prefix when it ends in a colon. */
export function invalidate(keyOrPrefix: string): void {
  if (keyOrPrefix.endsWith(':')) {
    for (const key of [...store.keys()]) {
      if (key.startsWith(keyOrPrefix)) drop(key)
    }
    return
  }
  drop(keyOrPrefix)
}

function drop(key: string): void {
  const entry = store.get(key)
  if (!entry) return
  if (entry.subs.size > 0) {
    // Someone is watching: blank the value so they refetch, rather than
    // deleting the entry out from under their subscription.
    commit(key, { value: undefined, producedAt: 0, error: undefined })
    return
  }
  store.delete(key)
}

export function clear(): void {
  store.clear()
}

export function subscribe(key: string, onChange: () => void): () => void {
  const entry = entryFor(key)
  entry.subs.add(onChange)
  return () => {
    entry.subs.delete(onChange)
  }
}

/** Test seam. */
export function _size(): number {
  return store.size
}
