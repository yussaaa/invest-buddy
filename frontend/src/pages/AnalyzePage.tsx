import { useState } from 'react'
import { Search, Loader2, AlertCircle } from 'lucide-react'
import { useAnalysis } from '../hooks/useAnalysis'
import { useSSE } from '../hooks/useSSE'
import AgentStatusTracker from '../components/analysis/AgentStatusTracker'
import FinalReportView from '../components/analysis/FinalReportView'
import SourceCitations from '../components/analysis/SourceCitations'
import DisclaimerBanner from '../components/ui/DisclaimerBanner'
import FeedbackButtons from '../components/ui/FeedbackButtons'

const USER_ID = 'anonymous'

export default function AnalyzePage() {
  const [ticker, setTicker] = useState('')
  const [query, setQuery] = useState('')

  const {
    startAnalysis,
    handleSSEEvent,
    result,
    isRunning,
    error,
    agentProgress,
    streamUrl,
    runId,
  } = useAnalysis()

  // Connect SSE stream when streamUrl is set
  useSSE(streamUrl, handleSSEEvent)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const t = ticker.trim().toUpperCase()
    const q = query.trim()
    if (!t || !q) return
    startAnalysis(t, q, USER_ID)
  }

  const finalReport = result?.result?.final_report ?? null
  const allCitations = finalReport?.citations ?? []

  return (
    <div className="max-w-4xl mx-auto px-6 py-8 flex flex-col gap-7">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-white">Stock Analysis</h1>
        <p className="text-sm text-slate-400 mt-1">
          Enter a ticker and your question — our AI agents will research and synthesize an answer.
        </p>
      </div>

      {/* Input form */}
      <form
        onSubmit={handleSubmit}
        className="rounded-xl border border-slate-700 bg-slate-800/60 p-5 flex flex-col gap-4"
      >
        <div className="flex gap-3 flex-col sm:flex-row">
          {/* Ticker */}
          <div className="flex flex-col gap-1.5 sm:w-40">
            <label htmlFor="ticker" className="text-xs font-medium text-slate-400 uppercase tracking-wide">
              Ticker
            </label>
            <input
              id="ticker"
              type="text"
              value={ticker}
              onChange={e => setTicker(e.target.value.toUpperCase())}
              placeholder="AAPL"
              maxLength={10}
              disabled={isRunning}
              className="rounded-lg border border-slate-600 bg-slate-900 px-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/60 disabled:opacity-50 font-mono uppercase"
            />
          </div>

          {/* Query */}
          <div className="flex flex-col gap-1.5 flex-1">
            <label htmlFor="query" className="text-xs font-medium text-slate-400 uppercase tracking-wide">
              Your Question
            </label>
            <input
              id="query"
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="e.g. What is the growth outlook and key risks for this stock?"
              disabled={isRunning}
              className="rounded-lg border border-slate-600 bg-slate-900 px-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/60 disabled:opacity-50"
            />
          </div>
        </div>

        {/* Additional context textarea */}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="context" className="text-xs font-medium text-slate-400 uppercase tracking-wide">
            Additional Context <span className="text-slate-600 normal-case">(optional)</span>
          </label>
          <textarea
            id="context"
            rows={3}
            value={''}
            readOnly
            placeholder="Add any specific context or constraints for the analysis…"
            disabled={isRunning}
            className="rounded-lg border border-slate-600 bg-slate-900 px-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/60 disabled:opacity-50 resize-none"
          />
        </div>

        <div className="flex justify-end">
          <button
            type="submit"
            disabled={isRunning || !ticker.trim() || !query.trim()}
            className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isRunning ? (
              <>
                <Loader2 size={15} className="animate-spin" />
                Analyzing…
              </>
            ) : (
              <>
                <Search size={15} />
                Analyze
              </>
            )}
          </button>
        </div>
      </form>

      {/* Error state */}
      {error && (
        <div className="flex items-start gap-3 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3">
          <AlertCircle size={16} className="mt-0.5 shrink-0 text-red-400" />
          <div>
            <p className="text-sm font-medium text-red-300">Analysis failed</p>
            <p className="text-xs text-red-400/80 mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {/* Agent progress tracker */}
      {agentProgress.length > 0 && (
        <div className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wide">
            Agent Progress
          </h2>
          <AgentStatusTracker agents={agentProgress} />
        </div>
      )}

      {/* Results */}
      {finalReport && (
        <div className="flex flex-col gap-5">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-bold text-white">
              Analysis: {result?.ticker || ticker}
            </h2>
            {runId && (
              <FeedbackButtons runId={runId} userId={USER_ID} />
            )}
          </div>

          <FinalReportView report={finalReport} />
          <SourceCitations citations={allCitations} />
        </div>
      )}

      {/* Disclaimer */}
      <DisclaimerBanner />
    </div>
  )
}
