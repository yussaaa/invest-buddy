/**
 * Runs one research brief over SSE. State lives in a pure reducer
 * (lib/researchReducer.ts); this hook only owns the request.
 *
 * The last finished run per ticker is kept in a module-level map, so closing
 * the ticker panel and reopening it does not throw away a 20-second brief.
 */

import { useCallback, useEffect, useReducer, useRef } from 'react'
import { api } from '@/lib/api'
import { parseSSEStream } from '@/lib/sse'
import {
  initialResearchState,
  researchReducer,
  type ResearchState,
} from '@/lib/researchReducer'

const lastRun = new Map<string, ResearchState>()

export function useResearchStream(ticker: string | null) {
  const [state, dispatch] = useReducer(
    researchReducer,
    ticker ? lastRun.get(ticker) ?? initialResearchState : initialResearchState,
  )
  const abortRef = useRef<AbortController | null>(null)
  const stateRef = useRef(state)
  stateRef.current = state

  // Switching ticker shows that ticker's last brief, or nothing.
  useEffect(() => {
    abortRef.current?.abort()
    dispatch({ type: 'reset' })
    const previous = ticker ? lastRun.get(ticker) : undefined
    if (previous) {
      for (const [event, data] of replay(previous)) dispatch({ type: 'event', event, data })
    }
  }, [ticker])

  useEffect(() => () => abortRef.current?.abort(), [])

  const start = useCallback(async (question?: string) => {
    if (!ticker) return
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    dispatch({ type: 'begin', ticker, question: question?.trim() || null })

    try {
      const response = await api.signals.researchStream(
        { ticker, question: question?.trim() || undefined },
        controller.signal,
      )
      if (!response.ok || !response.body) {
        const detail = await response.text().catch(() => '')
        const reason = (() => {
          try { return JSON.parse(detail).detail as string } catch { return detail }
        })()
        throw new Error(reason || `Research failed (${response.status})`)
      }
      for await (const { event, data } of parseSSEStream(response.body, controller.signal)) {
        dispatch({ type: 'event', event, data: (data ?? {}) as Record<string, unknown> })
      }
    } catch (e) {
      if (controller.signal.aborted) return
      dispatch({ type: 'fail', message: e instanceof Error ? e.message : String(e) })
    }
  }, [ticker])

  // Remember finished runs.
  useEffect(() => {
    if (ticker && state.phase === 'done' && state.ticker === ticker) lastRun.set(ticker, state)
  }, [ticker, state])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    dispatch({ type: 'reset' })
  }, [])

  return { state, start, cancel }
}

/** Events that rebuild a remembered state through the same reducer. */
function replay(s: ResearchState): [string, Record<string, unknown>][] {
  return [
    ['start', { cached: s.cached, question: s.question }],
    ['done', { brief: s.brief, replaced: s.replaced, latency_ms: s.latencyMs, cached: s.cached }],
  ]
}
