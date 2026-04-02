import { useState } from 'react'
import { Search, Loader2, AlertCircle } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
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
        <h1 className="text-2xl font-bold text-foreground">Stock Analysis</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Enter a ticker and your question -- our AI agents will research and synthesize an answer.
        </p>
      </div>

      {/* Input form */}
      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex gap-3 flex-col sm:flex-row">
              {/* Ticker */}
              <div className="flex flex-col gap-1.5 sm:w-40">
                <Label htmlFor="ticker" className="text-xs font-medium uppercase tracking-wide">
                  Ticker
                </Label>
                <Input
                  id="ticker"
                  type="text"
                  value={ticker}
                  onChange={e => setTicker(e.target.value.toUpperCase())}
                  placeholder="AAPL"
                  maxLength={10}
                  disabled={isRunning}
                  className="font-mono uppercase"
                />
              </div>

              {/* Query */}
              <div className="flex flex-col gap-1.5 flex-1">
                <Label htmlFor="query" className="text-xs font-medium uppercase tracking-wide">
                  Your Question
                </Label>
                <Input
                  id="query"
                  type="text"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="e.g. What is the growth outlook and key risks for this stock?"
                  disabled={isRunning}
                />
              </div>
            </div>

            {/* Additional context textarea */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="context" className="text-xs font-medium uppercase tracking-wide">
                Additional Context <span className="text-muted-foreground/50 normal-case">(optional)</span>
              </Label>
              <Textarea
                id="context"
                rows={3}
                value={''}
                readOnly
                placeholder="Add any specific context or constraints for the analysis..."
                disabled={isRunning}
                className="resize-none"
              />
            </div>

            <div className="flex justify-end">
              <Button
                type="submit"
                disabled={isRunning || !ticker.trim() || !query.trim()}
              >
                {isRunning ? (
                  <>
                    <Loader2 size={15} className="animate-spin" />
                    Analyzing...
                  </>
                ) : (
                  <>
                    <Search size={15} />
                    Analyze
                  </>
                )}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {/* Error state */}
      {error && (
        <Alert variant="destructive">
          <AlertCircle size={16} />
          <AlertTitle>Analysis failed</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Agent progress tracker */}
      {agentProgress.length > 0 && (
        <div className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
            Agent Progress
          </h2>
          <AgentStatusTracker agents={agentProgress} />
        </div>
      )}

      {/* Results */}
      {finalReport && (
        <div className="flex flex-col gap-5">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-bold text-foreground">
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
