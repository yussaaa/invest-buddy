/**
 * useSSE — React hook for consuming Server-Sent Events.
 * Uses the browser's native EventSource API.
 */

import { useEffect, useRef, useState } from 'react'

export interface SSEState<T> {
  events: Array<{ event: string; data: T }>
  lastEvent: { event: string; data: T } | null
  isConnected: boolean
  error: string | null
}

export function useSSE<T = unknown>(url: string | null, onEvent?: (event: string, data: T) => void) {
  const [state, setState] = useState<SSEState<T>>({
    events: [],
    lastEvent: null,
    isConnected: false,
    error: null,
  })

  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!url) return

    const es = new EventSource(url)
    esRef.current = es

    es.onopen = () => {
      setState(s => ({ ...s, isConnected: true, error: null }))
    }

    es.onerror = () => {
      setState(s => ({ ...s, isConnected: false, error: 'SSE connection error' }))
      es.close()
    }

    es.onmessage = (e) => {
      try {
        const parsed = JSON.parse(e.data) as T
        setState(s => ({
          ...s,
          events: [...s.events, { event: 'message', data: parsed }],
          lastEvent: { event: 'message', data: parsed },
        }))
        onEvent?.('message', parsed)
      } catch {
        // ignore parse errors
      }
    }

    // Listen for named events
    const namedEvents = [
      'run_started', 'agent_started', 'agent_completed',
      'guardrails_passed', 'synthesis_started', 'complete', 'error',
    ]
    namedEvents.forEach(eventName => {
      es.addEventListener(eventName, (e: MessageEvent) => {
        try {
          const parsed = JSON.parse(e.data) as T
          setState(s => ({
            ...s,
            events: [...s.events, { event: eventName, data: parsed }],
            lastEvent: { event: eventName, data: parsed },
          }))
          onEvent?.(eventName, parsed)
          if (eventName === 'complete' || eventName === 'error') {
            es.close()
            setState(s => ({ ...s, isConnected: false }))
          }
        } catch {
          // ignore
        }
      })
    })

    return () => {
      es.close()
    }
  }, [url])

  return state
}
