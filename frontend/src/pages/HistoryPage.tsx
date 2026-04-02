import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clock, AlertCircle, Loader2, ChevronLeft, ChevronRight, Eye } from 'lucide-react'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { cn } from '@/lib/utils'
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
    <Badge variant="outline" className={cn('text-xs font-semibold', color)}>
      {pct}%
    </Badge>
  )
}

function StatusBadge({ status }: { status: AnalysisResult['status'] }) {
  const styles: Record<AnalysisResult['status'], string> = {
    completed: 'bg-green-500/20 text-green-300 hover:bg-green-500/20',
    running: 'bg-blue-500/20 text-blue-300 hover:bg-blue-500/20',
    error: 'bg-red-500/20 text-red-300 hover:bg-red-500/20',
  }
  return (
    <Badge variant="secondary" className={cn('text-[10px] uppercase tracking-wide', styles[status])}>
      {status}
    </Badge>
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
    navigate(`/analyze?ticker=${item.ticker}`)
  }

  return (
    <div className="max-w-4xl mx-auto px-6 py-8 flex flex-col gap-7">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-foreground">Analysis History</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {total > 0 ? `${total} analyses run` : 'Your past analyses will appear here.'}
        </p>
      </div>

      {/* Error */}
      {error && (
        <Alert variant="destructive">
          <AlertCircle size={16} />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center gap-3 text-muted-foreground py-16 justify-center">
          <Loader2 size={20} className="animate-spin" />
          <span>Loading history...</span>
        </div>
      )}

      {/* Empty state */}
      {!loading && items.length === 0 && !error && (
        <div className="text-center py-16">
          <Clock size={40} className="mx-auto text-muted-foreground/30 mb-4" />
          <p className="text-muted-foreground font-medium">No analyses yet</p>
          <p className="text-sm text-muted-foreground/60 mt-1">Run your first analysis to see it here.</p>
        </div>
      )}

      {/* Table */}
      {!loading && items.length > 0 && (
        <div className="rounded-xl border border-border overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ticker</TableHead>
                <TableHead>Query</TableHead>
                <TableHead className="text-center">Confidence</TableHead>
                <TableHead className="text-center">Status</TableHead>
                <TableHead className="text-center">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map(item => {
                const confidence = item.result?.final_report?.overall_confidence
                return (
                  <TableRow key={item.run_id}>
                    {/* Ticker */}
                    <TableCell>
                      <div className="flex flex-col gap-0.5">
                        <span className="font-mono font-bold text-foreground">{item.ticker || '---'}</span>
                        <span className="text-xs text-muted-foreground">
                          {new Date(item.created_at).toLocaleDateString('en-US', {
                            month: 'short',
                            day: 'numeric',
                            year: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit',
                          })}
                        </span>
                      </div>
                    </TableCell>

                    {/* Query */}
                    <TableCell>
                      <p className="text-sm text-foreground/80 truncate max-w-[300px]" title={item.query}>
                        {item.query || '---'}
                      </p>
                    </TableCell>

                    {/* Confidence */}
                    <TableCell className="text-center">
                      {typeof confidence === 'number' ? (
                        <ConfidencePill value={confidence} />
                      ) : (
                        <span className="text-xs text-muted-foreground/50">---</span>
                      )}
                    </TableCell>

                    {/* Status */}
                    <TableCell className="text-center">
                      <StatusBadge status={item.status} />
                    </TableCell>

                    {/* Action */}
                    <TableCell className="text-center">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleView(item)}
                        className="gap-1.5"
                      >
                        <Eye size={13} />
                        View
                      </Button>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Pagination */}
      {!loading && totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            Page {page + 1} of {totalPages}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage(p => p - 1)}
              disabled={page === 0}
              className="gap-1"
            >
              <ChevronLeft size={15} />
              Prev
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage(p => p + 1)}
              disabled={page >= totalPages - 1}
              className="gap-1"
            >
              Next
              <ChevronRight size={15} />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
