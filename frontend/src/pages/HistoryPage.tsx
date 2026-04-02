import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clock, AlertCircle, Loader2, ChevronLeft, ChevronRight, Eye } from 'lucide-react'
import { api } from '../lib/api'
import type { AnalysisResult } from '../lib/types'

const USER_ID = 'anonymous'
const PAGE_SIZE = 10

function ConfidencePill({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color =
    value >= 0.7
      ? 'bg-green-500/20 text-green-300 border-green-500/40'
      : value >= 0.4
      ? 'bg-yellow-500/20 text-yellow-300 border-yellow-500/40'
      : 'bg-red-500/20 text-red-300 border-red-500/40'

  return (
    <span className={['text-xs font-semibold px-2.5 py-1 rounded-full border', color].join(' ')}>
      {pct}%
    </span>
  )
}

function StatusBadge({ status }: { status: AnalysisResult['status'] }) {
  const styles: Record<AnalysisResult['status'], string> = {
    completed: 'bg-green-500/20 text-green-300',
    running: 'bg-blue-500/20 text-blue-300',
    error: 'bg-red-500/20 text-red-300',
  }
  return (
    <span className={['text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide', styles[status]].join(' ')}>
      {status}
    </span>
  )
}

export default function HistoryPage() {
  const navigate = useNavigate()
  const [items, setItems] = useState<AnalysisResult[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function load(p: number) {
    setLoading(true)
    setError(null)
    try {
      const data = await api.analysis.history(USER_ID, PAGE_SIZE)
      setItems(data.items.slice(p * PAGE_SIZE, (p + 1) * PAGE_SIZE))
      setTotal(data.total)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load history')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(page) }, [page])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  function handleView(item: AnalysisResult) {
    // Navigate to analyze page pre-filled with ticker
    navigate(`/analyze?ticker=${item.ticker}`)
  }

  return (
    <div className="max-w-4xl mx-auto px-6 py-8 flex flex-col gap-7">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white">Analysis History</h1>
        <p className="text-sm text-slate-400 mt-1">
          {total > 0 ? `${total} analyses run` : 'Your past analyses will appear here.'}
        </p>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-start gap-3 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3">
          <AlertCircle size={16} className="mt-0.5 shrink-0 text-red-400" />
          <p className="text-sm text-red-300">{error}</p>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center gap-3 text-slate-400 py-16 justify-center">
          <Loader2 size={20} className="animate-spin" />
          <span>Loading history…</span>
        </div>
      )}

      {/* Empty state */}
      {!loading && items.length === 0 && !error && (
        <div className="text-center py-16">
          <Clock size={40} className="mx-auto text-slate-700 mb-4" />
          <p className="text-slate-400 font-medium">No analyses yet</p>
          <p className="text-sm text-slate-600 mt-1">Run your first analysis to see it here.</p>
        </div>
      )}

      {/* Table */}
      {!loading && items.length > 0 && (
        <div className="rounded-xl border border-slate-700 overflow-hidden">
          {/* Table header */}
          <div className="grid grid-cols-[2fr_3fr_auto_auto_auto] gap-4 px-5 py-3 bg-slate-800/80 border-b border-slate-700">
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Ticker</span>
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Query</span>
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Confidence</span>
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Status</span>
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Action</span>
          </div>

          {/* Rows */}
          <div className="divide-y divide-slate-700/60">
            {items.map(item => {
              const confidence = item.result?.final_report?.overall_confidence
              return (
                <div
                  key={item.run_id}
                  className="grid grid-cols-[2fr_3fr_auto_auto_auto] gap-4 px-5 py-4 items-center hover:bg-slate-800/40 transition-colors"
                >
                  {/* Ticker */}
                  <div className="flex flex-col gap-0.5">
                    <span className="font-mono font-bold text-white">{item.ticker || '—'}</span>
                    <span className="text-xs text-slate-500">
                      {new Date(item.created_at).toLocaleDateString('en-US', {
                        month: 'short',
                        day: 'numeric',
                        year: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </span>
                  </div>

                  {/* Query */}
                  <p className="text-sm text-slate-300 truncate" title={item.query}>
                    {item.query || '—'}
                  </p>

                  {/* Confidence */}
                  <div className="flex justify-center">
                    {typeof confidence === 'number' ? (
                      <ConfidencePill value={confidence} />
                    ) : (
                      <span className="text-xs text-slate-600">—</span>
                    )}
                  </div>

                  {/* Status */}
                  <div className="flex justify-center">
                    <StatusBadge status={item.status} />
                  </div>

                  {/* Action */}
                  <button
                    onClick={() => handleView(item)}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-600 text-xs text-slate-300 hover:text-white hover:border-slate-400 hover:bg-slate-700 transition-colors"
                  >
                    <Eye size={13} />
                    View
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Pagination */}
      {!loading && totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-slate-500">
            Page {page + 1} of {totalPages}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setPage(p => p - 1)}
              disabled={page === 0}
              className="flex items-center gap-1 px-3 py-2 rounded-lg border border-slate-700 text-sm text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <ChevronLeft size={15} />
              Prev
            </button>
            <button
              onClick={() => setPage(p => p + 1)}
              disabled={page >= totalPages - 1}
              className="flex items-center gap-1 px-3 py-2 rounded-lg border border-slate-700 text-sm text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Next
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
