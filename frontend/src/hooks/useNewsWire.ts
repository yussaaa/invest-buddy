/**
 * The live news wire: the stored first page, then a stream that keeps it current.
 *
 * The first page comes through the client cache, so returning to /news paints
 * instantly. The stream opens once that page is in hand, passing the newest
 * stamp it holds as `since`, so the server sends only what is missing; every
 * reconnect does the same, which is why this uses fetch + parseSSEStream rather
 * than EventSource — EventSource reconnects on its own with the URL it was
 * first given, and would replay from a stale `since`.
 *
 * `arrivals` records when each streamed headline came in, for the row that
 * should flash. Headlines from the first page are not "arrivals".
 */

import { useEffect, useRef, useState } from 'react'
import { useCachedResource } from '@/hooks/useCachedResource'
import { api } from '@/lib/api'
import { K, TTL } from '@/lib/cacheKeys'
import { mergeHeadlines, newestStamp } from '@/lib/newsReducer'
import { parseSSEStream } from '@/lib/sse'
import type { NewsHeadline } from '@/lib/types'

export type WireStatus = 'connecting' | 'live' | 'reconnecting'

const FIRST_PAGE = 200
const BACKOFF_START_MS = 1_000
const BACKOFF_MAX_MS = 30_000

export interface NewsWire {
  headlines: NewsHeadline[]
  status: WireStatus
  arrivals: Map<string, number>
  isLoading: boolean
  error: string | null
}

export function useNewsWire(): NewsWire {
  const first = useCachedResource(K.newsFeed(), () => api.news.list({ limit: FIRST_PAGE }), {
    ttl: TTL.newsFeed,
    shouldCache: page => !page.error,
  })

  const [headlines, setHeadlines] = useState<NewsHeadline[]>(() => first.data?.headlines ?? [])
  const [status, setStatus] = useState<WireStatus>('connecting')
  const [arrivals, setArrivals] = useState<Map<string, number>>(() => new Map())
  const headlinesRef = useRef(headlines)
  headlinesRef.current = headlines

  // Fold in the first page whenever the cache hands us a (re)validated one.
  useEffect(() => {
    if (first.data?.headlines) setHeadlines(current => mergeHeadlines(current, first.data!.headlines))
  }, [first.data])

  const ready = first.data !== undefined || first.error !== undefined

  useEffect(() => {
    if (!ready) return
    const controller = new AbortController()
    let backoff = BACKOFF_START_MS

    async function run() {
      while (!controller.signal.aborted) {
        try {
          const response = await api.news.stream(newestStamp(headlinesRef.current), controller.signal)
          if (!response.ok || !response.body) throw new Error(`stream ${response.status}`)
          for await (const message of parseSSEStream(response.body, controller.signal)) {
            if (message.event === 'ready') {
              setStatus('live')
              backoff = BACKOFF_START_MS
            } else if (message.event === 'headlines') {
              const incoming = (message.data as { headlines?: NewsHeadline[] }).headlines ?? []
              if (!incoming.length) continue
              const known = new Set(headlinesRef.current.map(h => h.id))
              const now = Date.now()
              setArrivals(previous => {
                const next = new Map(previous)
                for (const h of incoming) if (!known.has(h.id)) next.set(h.id, now)
                return next
              })
              setHeadlines(current => mergeHeadlines(current, incoming))
            }
          }
        } catch {
          // Fall through to the backoff: a dropped connection, a 5xx, or the abort.
        }
        if (controller.signal.aborted) return
        setStatus('reconnecting')
        await new Promise(resolve => setTimeout(resolve, backoff))
        backoff = Math.min(backoff * 2, BACKOFF_MAX_MS)
      }
    }

    void run()
    return () => controller.abort()
  }, [ready])

  return {
    headlines,
    status,
    arrivals,
    isLoading: first.isLoading && headlines.length === 0,
    error: first.data?.error ?? first.error?.message ?? null,
  }
}
