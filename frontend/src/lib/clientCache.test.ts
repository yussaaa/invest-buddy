/**
 * Unit tests for the client cache — pure, no DOM, no network.
 *
 * The cache is where the bugs would be invisible to the typechecker and
 * awkward to reproduce by hand: TTL boundaries, two components racing for the
 * same cold key, and the rule that a failed revalidate must never take a good
 * value down with it. Each of those gets a test.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  _size,
  cached,
  clear,
  invalidate,
  isFresh,
  peek,
  put,
  revalidate,
  subscribe,
} from './clientCache'

afterEach(() => {
  clear()
  vi.useRealTimers()
})

/** A fetcher that counts its calls and resolves on demand. */
function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const opts = { ttl: 1000 }

describe('freshness', () => {
  it('fetches on a miss and serves the cached value inside the TTL', async () => {
    const fetcher = vi.fn().mockResolvedValue('first')

    expect(await cached('k', fetcher, opts)).toBe('first')
    expect(await cached('k', fetcher, opts)).toBe('first')

    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('refetches once the TTL has passed', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn().mockResolvedValueOnce('first').mockResolvedValueOnce('second')

    expect(await cached('k', fetcher, opts)).toBe('first')
    vi.advanceTimersByTime(1001)
    expect(await cached('k', fetcher, opts)).toBe('second')

    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('stamps producedAt with the START of the fetch, matching the backend', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)

    const d = deferred<string>()
    const promise = cached('k', () => d.promise, opts)

    vi.setSystemTime(1_000_500)   // half a second passes inside the fetch
    d.resolve('v')
    await promise

    expect(peek<string>('k').producedAt).toBe(1_000_000)
  })

  it('isFresh reflects the TTL', async () => {
    vi.useFakeTimers()
    await cached('k', () => Promise.resolve('v'), opts)

    expect(isFresh('k', 1000)).toBe(true)
    vi.advanceTimersByTime(1001)
    expect(isFresh('k', 1000)).toBe(false)
  })
})

describe('single-flight', () => {
  it('runs the fetcher once for concurrent callers on a cold key', async () => {
    const d = deferred<string>()
    const fetcher = vi.fn(() => d.promise)

    const a = cached('k', fetcher, opts)
    const b = cached('k', fetcher, opts)
    d.resolve('shared')

    expect(await a).toBe('shared')
    expect(await b).toBe('shared')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('revalidate ignores the TTL but still joins an in-flight request', async () => {
    const fetcher = vi.fn().mockResolvedValue('v')
    await cached('k', fetcher, opts)          // 1 — populates well inside the TTL

    const d = deferred<string>()
    const slow = vi.fn(() => d.promise)
    const a = revalidate('k', slow, opts)     // 2 — forced despite being fresh
    const b = revalidate('k', slow, opts)     // joins, does not start a third
    d.resolve('v2')
    await Promise.all([a, b])

    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(slow).toHaveBeenCalledTimes(1)
    expect(peek<string>('k').value).toBe('v2')
  })
})

describe('failure handling', () => {
  it('keeps the last good value when a revalidate fails', async () => {
    vi.useFakeTimers()
    await cached('k', () => Promise.resolve('good'), opts)
    vi.advanceTimersByTime(1001)

    await expect(cached('k', () => Promise.reject(new Error('boom')), opts)).rejects.toThrow('boom')

    const snapshot = peek<string>('k')
    expect(snapshot.value).toBe('good')       // the point of the whole test
    expect(snapshot.error?.message).toBe('boom')
    expect(snapshot.isFetching).toBe(false)
  })

  it('clears the error once a later fetch succeeds', async () => {
    await expect(cached('k', () => Promise.reject(new Error('boom')), opts)).rejects.toThrow()
    expect(peek('k').error).toBeDefined()

    await cached('k', () => Promise.resolve('ok'), opts)
    expect(peek('k').error).toBeUndefined()
  })

  it('wraps a non-Error rejection', async () => {
    await expect(cached('k', () => Promise.reject('a string'), opts)).rejects.toBeDefined()
    expect(peek('k').error).toBeInstanceOf(Error)
  })
})

describe('shouldCache', () => {
  it('does not pin a value the guard rejects, and retries next time', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({ rows: [1] })
    const guard = { ttl: 1000, shouldCache: (v: { rows: number[] }) => v.rows.length > 0 }

    await cached('k', fetcher, guard)
    expect(peek('k').value).toBeUndefined()
    expect(peek('k').isFetching).toBe(false)

    await cached('k', fetcher, guard)
    expect(peek<{ rows: number[] }>('k').value).toEqual({ rows: [1] })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
})

describe('subscriptions', () => {
  it('notifies on commit and stops after unsubscribe', async () => {
    const onChange = vi.fn()
    const unsubscribe = subscribe('k', onChange)

    await cached('k', () => Promise.resolve('v'), opts)
    expect(onChange).toHaveBeenCalled()

    const seen = onChange.mock.calls.length
    unsubscribe()
    put('k', 'v2')
    expect(onChange).toHaveBeenCalledTimes(seen)
  })

  it('survives a subscriber unsubscribing from inside its own callback', () => {
    const unsubscribe = subscribe('k', () => unsubscribe())
    expect(() => put('k', 'v')).not.toThrow()
  })

  it('returns a referentially identical snapshot when nothing has changed', async () => {
    // useSyncExternalStore throws "getSnapshot should be cached" otherwise.
    await cached('k', () => Promise.resolve('v'), opts)
    expect(peek('k')).toBe(peek('k'))
  })

  it('hands back a stable empty snapshot for an unknown key', () => {
    expect(peek('never-seen')).toBe(peek('also-never-seen'))
  })
})

describe('invalidate', () => {
  it('drops an exact key', async () => {
    await cached('k', () => Promise.resolve('v'), opts)
    invalidate('k')
    expect(peek('k').value).toBeUndefined()
  })

  it('drops every key under a prefix', async () => {
    await cached('movers:all:10', () => Promise.resolve('a'), opts)
    await cached('movers:mid:10', () => Promise.resolve('b'), opts)
    await cached('overview', () => Promise.resolve('c'), opts)

    invalidate('movers:')

    expect(peek('movers:all:10').value).toBeUndefined()
    expect(peek('movers:mid:10').value).toBeUndefined()
    expect(peek('overview').value).toBe('c')
  })

  it('blanks rather than deletes a key someone is watching', async () => {
    await cached('k', () => Promise.resolve('v'), opts)
    const unsubscribe = subscribe('k', () => {})

    invalidate('k')

    expect(peek('k').value).toBeUndefined()
    expect(_size()).toBe(1)   // entry kept so the subscription stays wired
    unsubscribe()
  })
})

describe('eviction', () => {
  it('never evicts an entry with a live subscriber', async () => {
    const unsubscribe = subscribe('pinned', () => {})
    await cached('pinned', () => Promise.resolve('keep me'), opts)

    for (let i = 0; i < 200; i++) {
      await cached(`filler:${i}`, () => Promise.resolve(i), opts)
    }

    expect(peek<string>('pinned').value).toBe('keep me')
    unsubscribe()
  })
})
