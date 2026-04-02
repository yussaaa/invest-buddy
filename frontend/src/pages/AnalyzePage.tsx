import { useState, useRef } from 'react'
import { Search, Loader2, AlertCircle, FileText, Upload, X } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import { api } from '../lib/api'
import { useAnalysis } from '../hooks/useAnalysis'
import { useSSE } from '../hooks/useSSE'
import AgentStatusTracker from '../components/analysis/AgentStatusTracker'
import FinalReportView from '../components/analysis/FinalReportView'
import SourceCitations from '../components/analysis/SourceCitations'
import DisclaimerBanner from '../components/ui/DisclaimerBanner'
import FeedbackButtons from '../components/ui/FeedbackButtons'

const USER_ID = 'anonymous'

const ANALYSIS_TYPES = [
  {
    value: 'full',
    label: 'Full Analysis',
    query: 'Provide a comprehensive analysis covering fundamentals, technicals, sentiment, risk, and market context.',
    supportsRag: true,
  },
  {
    value: 'fundamental',
    label: 'Fundamental Analysis',
    query: 'Analyze the financial statements, valuation ratios, earnings, and fundamental health.',
    supportsRag: false,
  },
  {
    value: 'technical',
    label: 'Technical Analysis',
    query: 'Analyze the price action, technical indicators (RSI, MACD, Bollinger Bands), moving averages, and chart patterns.',
    supportsRag: false,
  },
  {
    value: 'risk',
    label: 'Risk Assessment',
    query: 'Assess the risk profile including volatility, VaR, beta, Sharpe ratio, and maximum drawdown.',
    supportsRag: false,
  },
  {
    value: 'sentiment',
    label: 'Sentiment Analysis',
    query: 'Analyze market sentiment from news, analyst ratings, and options market activity.',
    supportsRag: true,
  },
  {
    value: 'market_research',
    label: 'Market Research',
    query: 'Research the company overview, recent news, SEC filings, competitive landscape, and upcoming events.',
    supportsRag: true,
  },
  {
    value: 'other',
    label: 'Other (Custom)',
    query: '',
    supportsRag: true,
  },
]

// Types that show the RAG toggle
const RAG_TYPES = new Set(ANALYSIS_TYPES.filter(t => t.supportsRag).map(t => t.value))

interface UploadedFile {
  file_id: string
  filename: string
  size_bytes: number
}

export default function AnalyzePage() {
  const [ticker, setTicker] = useState('')
  const [analysisType, setAnalysisType] = useState('full')
  const [customInstructions, setCustomInstructions] = useState('')
  const [useRag, setUseRag] = useState(false)
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([])
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

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

  useSSE(streamUrl, handleSSEEvent)

  // Disclaimer: only show on first visit
  const [showDisclaimer, setShowDisclaimer] = useState(() => {
    return localStorage.getItem('agent-invest-disclaimer-seen') !== 'true'
  })

  function dismissDisclaimer() {
    localStorage.setItem('agent-invest-disclaimer-seen', 'true')
    setShowDisclaimer(false)
  }

  const isOther = analysisType === 'other'
  const showRagToggle = RAG_TYPES.has(analysisType)

  // Reset RAG toggle when switching to a type that doesn't support it
  function handleAnalysisTypeChange(value: string) {
    setAnalysisType(value)
    if (!RAG_TYPES.has(value)) {
      setUseRag(false)
      setUploadedFiles([])
    }
  }

  async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files
    if (!files || files.length === 0) return

    setUploading(true)
    try {
      for (const file of Array.from(files)) {
        const result = await api.upload.file(file)
        setUploadedFiles(prev => [...prev, result])
      }
    } catch (err) {
      console.error('Upload failed:', err)
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  function removeFile(fileId: string) {
    setUploadedFiles(prev => prev.filter(f => f.file_id !== fileId))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const t = ticker.trim().toUpperCase()
    if (!t) return

    let query: string
    if (isOther) {
      query = customInstructions.trim() || 'Provide a comprehensive analysis.'
    } else {
      const preset = ANALYSIS_TYPES.find(a => a.value === analysisType)
      query = preset?.query || 'Provide a comprehensive analysis.'
      if (customInstructions.trim()) {
        query += `\n\nAdditional instructions: ${customInstructions.trim()}`
      }
    }

    const fileIds = uploadedFiles.map(f => f.file_id)
    startAnalysis(t, query, USER_ID, undefined, useRag, fileIds)
  }

  const finalReport = result?.result?.final_report ?? null
  const allCitations = finalReport?.citations ?? []

  return (
    <div className="max-w-4xl mx-auto px-6 py-8 flex flex-col gap-7">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-foreground">Stock Analysis</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Enter a ticker, choose an analysis type, and let our AI agents do the research.
        </p>
      </div>

      {/* Input form */}
      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            {/* Row 1: Ticker + Analysis Type */}
            <div className="flex gap-3 flex-col sm:flex-row">
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

              <div className="flex flex-col gap-1.5 flex-1">
                <Label htmlFor="analysis-type" className="text-xs font-medium uppercase tracking-wide">
                  Analysis Type
                </Label>
                <select
                  id="analysis-type"
                  value={analysisType}
                  onChange={e => handleAnalysisTypeChange(e.target.value)}
                  disabled={isRunning}
                  className={cn(
                    "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
                    "ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                    "disabled:cursor-not-allowed disabled:opacity-50",
                    "text-foreground"
                  )}
                >
                  {ANALYSIS_TYPES.map(t => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Row 2: Custom Instructions */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="custom-instructions" className="text-xs font-medium uppercase tracking-wide">
                Custom Instructions{' '}
                {isOther
                  ? <span className="text-destructive normal-case">(required)</span>
                  : <span className="text-muted-foreground/50 normal-case">(optional)</span>
                }
              </Label>
              <Textarea
                id="custom-instructions"
                rows={3}
                value={customInstructions}
                onChange={e => setCustomInstructions(e.target.value)}
                placeholder={
                  isOther
                    ? "Describe what you want to analyze — this will be sent directly as the query..."
                    : "Add any specific focus areas, constraints, or questions for the analysis..."
                }
                disabled={isRunning}
                className="resize-none"
              />
            </div>

            {/* Row 3: Document Research Toggle + File Upload */}
            {showRagToggle && (
              <>
                <Separator />
                <div className="flex flex-col gap-3">
                  {/* Toggle */}
                  <label className="flex items-center gap-3 cursor-pointer select-none">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={useRag}
                      onClick={() => {
                        setUseRag(!useRag)
                        if (useRag) setUploadedFiles([])  // clear files when toggling off
                      }}
                      disabled={isRunning}
                      className={cn(
                        "relative inline-flex h-6 w-11 shrink-0 rounded-full border-2 border-transparent transition-colors",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                        "disabled:cursor-not-allowed disabled:opacity-50",
                        useRag ? "bg-primary" : "bg-muted"
                      )}
                    >
                      <span
                        className={cn(
                          "pointer-events-none inline-block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition-transform",
                          useRag ? "translate-x-5" : "translate-x-0"
                        )}
                      />
                    </button>
                    <div className="flex flex-col">
                      <span className="text-sm font-medium text-foreground">
                        Include Document Research
                      </span>
                      <span className="text-xs text-muted-foreground">
                        Ingests SEC filings and news into the knowledge base for grounded analysis
                        {useRag && ' (may add ~15-30s on first run for a new ticker)'}
                      </span>
                    </div>
                  </label>

                  {/* File Upload (shown when toggle is ON) */}
                  {useRag && (
                    <div className="flex flex-col gap-2 pl-14">
                      {/* Uploaded files list */}
                      {uploadedFiles.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {uploadedFiles.map(f => (
                            <Badge
                              key={f.file_id}
                              variant="secondary"
                              className="flex items-center gap-1.5 py-1 pl-2 pr-1"
                            >
                              <FileText size={12} />
                              <span className="text-xs max-w-[150px] truncate">{f.filename}</span>
                              <button
                                type="button"
                                onClick={() => removeFile(f.file_id)}
                                className="ml-0.5 rounded-sm hover:bg-muted-foreground/20 p-0.5"
                              >
                                <X size={12} />
                              </button>
                            </Badge>
                          ))}
                        </div>
                      )}

                      {/* Upload button */}
                      <div className="flex items-center gap-2">
                        <input
                          ref={fileInputRef}
                          type="file"
                          accept=".txt,.pdf,.csv,.md,.html"
                          multiple
                          onChange={handleFileUpload}
                          disabled={isRunning || uploading}
                          className="hidden"
                        />
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => fileInputRef.current?.click()}
                          disabled={isRunning || uploading}
                        >
                          {uploading ? (
                            <Loader2 size={14} className="animate-spin" />
                          ) : (
                            <Upload size={14} />
                          )}
                          {uploading ? 'Uploading...' : 'Upload your files'}
                        </Button>
                        <span className="text-xs text-muted-foreground">
                          TXT, PDF, CSV, MD, HTML (max 10MB)
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}

            {/* Row 4: Full-width Analyze button */}
            <Button
              type="submit"
              disabled={isRunning || !ticker.trim() || (isOther && !customInstructions.trim())}
              className="w-full"
              size="lg"
            >
              {isRunning ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  {useRag ? 'Indexing & Analyzing...' : 'Analyzing...'}
                </>
              ) : (
                <>
                  <Search size={16} />
                  Analyze{useRag ? ' with Document Research' : ''}
                </>
              )}
            </Button>
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

      {/* Disclaimer — only on first visit */}
      {showDisclaimer && <DisclaimerBanner onDismiss={dismissDisclaimer} />}
    </div>
  )
}
