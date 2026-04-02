/**
 * useAnalysis — manages the full analysis lifecycle:
 * trigger → poll SSE stream → return completed result.
 */

import { useState, useCallback } from 'react'
import { api } from '../lib/api'
import type { AgentProgress, AgentName, AnalysisResult } from '../lib/types'

const AGENT_NAMES: AgentName[] = ['market_research', 'sentiment', 'fundamental', 'technical', 'risk']

export function useAnalysis() {
  const [runId, setRunId] = useState<string | null>(null)
  const [streamUrl, setStreamUrl] = useState<string | null>(null)
  const [agentProgress, setAgentProgress] = useState<AgentProgress[]>([])
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [isRunning, setIsRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const startAnalysis = useCallback(async (
    ticker: string,
    query: string,
    userId = 'anonymous',
    depth?: string,
  ) => {
    setIsRunning(true)
    setError(null)
    setResult(null)
    setAgentProgress(AGENT_NAMES.map(name => ({ name, status: 'pending' })))

    try {
      const { run_id } = await api.analysis.trigger(ticker, query, userId, depth)
      setRunId(run_id)
      setStreamUrl(`/api/v1/analysis/${run_id}/stream`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start analysis')
      setIsRunning(false)
    }
  }, [])

  const handleSSEEvent = useCallback((event: string, data: unknown) => {
    const d = data as Record<string, unknown>

    if (event === 'agent_completed') {
      const agentKey = d.agent as string
      setAgentProgress(prev =>
        prev.map(a =>
          a.name === agentKey
            ? { ...a, status: 'completed', confidence: d.confidence as number }
            : a
        )
      )
    }

    if (event === 'complete') {
      const payload = d as { result: AnalysisResult['result']; run_id: string }
      setResult({
        run_id: payload.run_id,
        status: 'completed',
        ticker: '',
        query: '',
        created_at: new Date().toISOString(),
        result: payload.result,
      })
      setIsRunning(false)
      setStreamUrl(null)
    }

    if (event === 'error') {
      setError((d.message as string) || 'Analysis failed')
      setIsRunning(false)
      setStreamUrl(null)
    }
  }, [])

  return {
    runId,
    streamUrl,
    agentProgress,
    result,
    isRunning,
    error,
    startAnalysis,
    handleSSEEvent,
  }
}
