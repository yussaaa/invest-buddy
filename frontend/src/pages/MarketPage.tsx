/**
 * MarketPage — overall market conditions: index cards, a finviz-style heatmap
 * with index/timeframe pickers, sector performance, macro benchmarks, and the
 * week's earnings and economic releases.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  CalendarDays,
  Loader2,
  RefreshCw,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import type { Heatmap as HeatmapData, MarketEvents, MarketOverview, MarketRow } from '@/lib/types'
import Heatmap from '@/components/market/Heatmap'

const INDEX_OPTIONS = [
  { value: 'sp500', label: 'S&P 500' },
  { value: 'nasdaq100', label: 'Nasdaq 100' },
  { value: 'dow30', label: 'Dow Jones 30' },
]

const HEATMAP_RANGES = ['1D', '1W', '1M', '3M', '6M', 'YTD', '1Y']

function tone(v?: number | null) {
  if (v == null || v === 0) return 'text-muted-foreground'
  return v > 0 ? 'text-emerald-400' : 'text-rose-400'
}

function pct(v?: number | null): string {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

function num(v?: number | null): string {
  if (v == null) return '—'
  return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** Format a YYYY-MM-DD calendar date without letting UTC parsing shift the day. */
function formatDay(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return iso
  return new Date(y, m - 1, d).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })
}

/** Inline sparkline — the last ~30 daily closes. Scales to its container. */
function Sparkline({
  values,
  positive,
  className = 'h-7 w-full',
}: {
  values: number[]
  positive: boolean
  className?: string
}) {
  if (values.length < 2) return null
  const w = 110
  const h = 30
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const points = values
    .map((v, i) => `${(i / (values.length - 1)) * w},${h - ((v - min) / span) * h}`)
    .join(' ')

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className={cn('shrink-0', className)}
    >
      <polyline
        points={points}
        fill="none"
        strokeWidth={1.5}
        vectorEffect="non-scaling-stroke"
        stroke={positive ? '#34d399' : '#fb7185'}
      />
    </svg>
  )
}

function IndexCard({ row, onClick }: { row: MarketRow; onClick: () => void }) {
  const up = (row.change_percent ?? 0) > 0
  return (
    <Card
      onClick={onClick}
      className="cursor-pointer transition-colors hover:border-primary/40"
    >
      <CardContent className="flex flex-col gap-2 py-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-[13px] font-semibold leading-tight text-foreground">{row.label}</p>
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
              {row.note ?? row.symbol}
            </p>
          </div>
          {up ? (
            <TrendingUp size={15} className="shrink-0 text-emerald-400" />
          ) : (
            <TrendingDown size={15} className="shrink-0 text-rose-400" />
          )}
        </div>

        <div>
          <p className="text-xl font-bold tabular-nums text-foreground">{num(row.last)}</p>
          <p className={cn('text-xs font-medium tabular-nums', tone(row.change_percent))}>
            {row.change != null ? `${row.change > 0 ? '+' : ''}${num(row.change)} ` : ''}
            ({pct(row.change_percent)})
          </p>
        </div>

        <Sparkline values={row.sparkline ?? []} positive={up} />

        <p className="text-[10px] text-muted-foreground/60">
          1M {pct(row.period_change_percent)}
        </p>
      </CardContent>
    </Card>
  )
}

/**
 * yfinance drops symbols from a batch now and then. Rather than blank the row
 * until the next poll, carry the previous value forward.
 */
function mergeRows(prev: MarketRow[], next: MarketRow[]): MarketRow[] {
  const previous = new Map(prev.map(r => [r.symbol, r]))
  return next.map(row => (row.last == null ? previous.get(row.symbol) ?? row : row))
}

function mergeOverview(prev: MarketOverview | null, next: MarketOverview): MarketOverview {
  if (!prev) return next
  return {
    ...next,
    indices: mergeRows(prev.indices, next.indices),
    sectors: mergeRows(prev.sectors, next.sectors),
    macro: mergeRows(prev.macro, next.macro),
  }
}

function PerformanceBar({ row }: { row: MarketRow }) {
  const v = row.change_percent ?? 0
  const width = Math.min(100, Math.abs(v) * 25)  // ±4% fills the half-bar
  return (
    <div className="flex items-center gap-3">
      <span className="w-32 shrink-0 truncate text-xs text-foreground">{row.label}</span>
      <div className="relative h-4 flex-1 rounded bg-muted/40">
        <div className="absolute left-1/2 top-0 h-full w-px bg-border" />
        <div
          className={cn('absolute top-0 h-full rounded', v > 0 ? 'bg-emerald-500/60' : 'bg-rose-500/60')}
          style={
            v > 0
              ? { left: '50%', width: `${width / 2}%` }
              : { right: '50%', width: `${width / 2}%` }
          }
        />
      </div>
      <span className={cn('w-14 shrink-0 text-right text-xs tabular-nums', tone(v))}>{pct(v)}</span>
    </div>
  )
}

export default function MarketPage() {
  const navigate = useNavigate()

  const [overview, setOverview] = useState<MarketOverview | null>(null)
  const [overviewError, setOverviewError] = useState<string | null>(null)
  const [loadingOverview, setLoadingOverview] = useState(true)

  const [index, setIndex] = useState('sp500')
  const [range, setRange] = useState('1D')
  const [heatmap, setHeatmap] = useState<HeatmapData | null>(null)
  const [loadingHeatmap, setLoadingHeatmap] = useState(true)
  const [heatmapError, setHeatmapError] = useState<string | null>(null)

  const [events, setEvents] = useState<MarketEvents | null>(null)
  const [loadingEvents, setLoadingEvents] = useState(true)

  // Overview — refreshed on a slow poll
  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const data = await api.market.overview()
        if (!cancelled) {
          setOverview(prev => mergeOverview(prev, data))
          setOverviewError(data.error ?? null)
        }
      } catch (e) {
        if (!cancelled) setOverviewError(e instanceof Error ? e.message : 'Failed to load market data')
      } finally {
        if (!cancelled) setLoadingOverview(false)
      }
    }

    load()
    const id = setInterval(load, 30000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  // Heatmap — reloads whenever the index or window changes
  useEffect(() => {
    const controller = new AbortController()
    setLoadingHeatmap(true)
    setHeatmapError(null)

    api.market
      .heatmap(index, range, 150, controller.signal)
      .then(data => {
        if (controller.signal.aborted) return
        setHeatmap(data)
        setHeatmapError(data.error ?? null)
      })
      .catch(e => {
        if (!controller.signal.aborted) {
          setHeatmapError(e instanceof Error ? e.message : 'Failed to load heatmap')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingHeatmap(false)
      })

    return () => controller.abort()
  }, [index, range])

  useEffect(() => {
    api.market
      .events(7)
      .then(setEvents)
      .catch(() => setEvents(null))
      .finally(() => setLoadingEvents(false))
  }, [])

  const breadth = overview?.breadth
  const earnings = events?.earnings ?? []
  const economic = events?.economic

  return (
    <div className="max-w-[1500px] mx-auto px-6 py-6 flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Market Overview</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Index conditions, sector rotation and the week ahead.
          </p>
        </div>
        {breadth && (
          <Badge variant="secondary" className="gap-1.5">
            <RefreshCw size={11} />
            {breadth.sectors_advancing}/{breadth.sectors_total} sectors advancing
          </Badge>
        )}
      </div>

      {overviewError && (
        <Alert variant="destructive">
          <AlertCircle size={16} />
          <AlertDescription>{overviewError}</AlertDescription>
        </Alert>
      )}

      {/* Index cards */}
      {loadingOverview && !overview ? (
        <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
          <Loader2 size={16} className="animate-spin" />
          Loading market data…
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          {(overview?.indices ?? []).map(row => (
            <IndexCard
              key={row.symbol}
              row={row}
              onClick={() => navigate(`/charting?ticker=${encodeURIComponent(row.symbol)}`)}
            />
          ))}
        </div>
      )}

      {/* Heatmap */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Heatmap
          </h2>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1 rounded-lg border border-border bg-card p-1">
              {INDEX_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setIndex(opt.value)}
                  className={cn(
                    'h-7 rounded px-2.5 text-xs font-medium transition-colors',
                    index === opt.value
                      ? 'bg-primary/20 text-primary'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-1 rounded-lg border border-border bg-card p-1">
              {HEATMAP_RANGES.map(r => (
                <button
                  key={r}
                  onClick={() => setRange(r)}
                  className={cn(
                    'h-7 rounded px-2 text-xs font-medium transition-colors',
                    range === r
                      ? 'bg-primary/20 text-primary'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  )}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>
        </div>

        <Card>
          <CardContent className="p-3">
            {heatmapError && (
              <Alert variant="destructive" className="mb-3">
                <AlertCircle size={16} />
                <AlertDescription>{heatmapError}</AlertDescription>
              </Alert>
            )}

            {loadingHeatmap && !heatmap?.tiles.length ? (
              <div className="flex h-[620px] flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 size={18} className="animate-spin" />
                Building heatmap…
                <span className="text-xs text-muted-foreground/60">
                  First load fetches index membership and market caps
                </span>
              </div>
            ) : (
              <>
                <Heatmap
                  tiles={heatmap?.tiles ?? []}
                  height={620}
                  onSelect={symbol => navigate(`/charting?ticker=${encodeURIComponent(symbol)}`)}
                />
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2 px-1 text-[10px] text-muted-foreground/60">
                  <span>
                    {heatmap?.index_label} · top {heatmap?.tiles.length} of{' '}
                    {heatmap?.universe_size} by market cap · {range} performance
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="text-rose-400">▉ −3%</span>
                    <span>▉ 0%</span>
                    <span className="text-emerald-400">▉ +3%</span>
                    <span>
                      {heatmap?.advancing} up / {heatmap?.declining} down
                    </span>
                  </span>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Sectors + macro */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardContent className="flex flex-col gap-3 py-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-foreground">Sector performance</h2>
              <span className="text-[10px] text-muted-foreground/60">Today</span>
            </div>
            <Separator />
            <div className="flex flex-col gap-2">
              {(overview?.sectors ?? []).map(row => (
                <PerformanceBar key={row.symbol} row={row} />
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-3 py-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-foreground">Rates, commodities & crypto</h2>
              <span className="text-[10px] text-muted-foreground/60">Today</span>
            </div>
            <Separator />
            <div className="flex flex-col">
              {(overview?.macro ?? []).map(row => (
                <div
                  key={row.symbol}
                  className="flex items-center justify-between gap-2 border-b border-border/40 py-2 last:border-b-0"
                >
                  <span className="text-xs text-foreground">{row.label}</span>
                  <div className="flex items-center gap-3">
                    <Sparkline
                      values={row.sparkline ?? []}
                      positive={(row.change_percent ?? 0) > 0}
                      className="hidden h-7 w-24 sm:block"
                    />
                    <span className="w-20 text-right text-xs tabular-nums text-foreground">
                      {num(row.last)}
                      {row.unit ?? ''}
                    </span>
                    <span
                      className={cn(
                        'w-16 text-right text-xs tabular-nums',
                        tone(row.change_percent)
                      )}
                    >
                      {pct(row.change_percent)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Week ahead */}
      <div className="flex flex-col gap-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <CalendarDays size={14} />
          Week ahead
          {events && (
            <span className="normal-case tracking-normal text-[11px] text-muted-foreground/60">
              {events.week_start} → {events.week_end}
            </span>
          )}
        </h2>

        <div className="grid gap-4 lg:grid-cols-2">
          {/* Earnings */}
          <Card>
            <CardContent className="flex flex-col gap-3 py-4">
              <h3 className="text-sm font-semibold text-foreground">
                Earnings <span className="text-muted-foreground/60">({earnings.length})</span>
              </h3>
              <Separator />

              {loadingEvents ? (
                <div className="flex items-center gap-2 py-6 text-xs text-muted-foreground">
                  <Loader2 size={14} className="animate-spin" />
                  Scanning large caps…
                </div>
              ) : earnings.length === 0 ? (
                <p className="py-6 text-center text-xs text-muted-foreground/60">
                  No large-cap reports scheduled in the next 7 days.
                </p>
              ) : (
                <div className="flex flex-col">
                  {earnings.map(e => (
                    <button
                      key={`${e.symbol}-${e.date}`}
                      onClick={() => navigate(`/charting?ticker=${e.symbol}`)}
                      className="flex items-center justify-between gap-2 border-b border-border/40 py-2 text-left last:border-b-0 hover:bg-muted/40"
                    >
                      <div className="flex items-center gap-2.5">
                        <Badge variant="outline" className="font-mono text-[11px]">
                          {e.symbol}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {formatDay(e.date)}
                        </span>
                      </div>
                      <span className="text-[11px] tabular-nums text-muted-foreground">
                        {e.eps_estimate != null ? `EPS est. ${e.eps_estimate.toFixed(2)}` : ''}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Economic releases */}
          <Card>
            <CardContent className="flex flex-col gap-3 py-4">
              <h3 className="text-sm font-semibold text-foreground">
                Economic releases{' '}
                <span className="text-muted-foreground/60">({economic?.events.length ?? 0})</span>
              </h3>
              <Separator />

              {loadingEvents ? (
                <div className="flex items-center gap-2 py-6 text-xs text-muted-foreground">
                  <Loader2 size={14} className="animate-spin" />
                  Loading calendar…
                </div>
              ) : !economic?.available ? (
                <div className="py-6 text-center">
                  <p className="text-xs text-muted-foreground">Economic calendar not configured</p>
                  <p className="mt-1 text-[11px] text-muted-foreground/60">
                    Set <code className="font-mono">FRED_API_KEY</code> in .env to pull release
                    dates from the St. Louis Fed.
                  </p>
                </div>
              ) : economic.events.length === 0 ? (
                <p className="py-6 text-center text-xs text-muted-foreground/60">
                  No major releases in the next 7 days.
                </p>
              ) : (
                <div className="flex flex-col">
                  {economic.events.map(e => (
                    <div
                      key={`${e.name}-${e.date}`}
                      className="flex items-center justify-between gap-3 border-b border-border/40 py-2 last:border-b-0"
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <span
                          className={cn(
                            'h-1.5 w-1.5 shrink-0 rounded-full',
                            e.importance === 'high' ? 'bg-amber-400' : 'bg-muted-foreground/40'
                          )}
                        />
                        <span className="truncate text-xs text-foreground">{e.name}</span>
                      </div>
                      <span className="shrink-0 text-[11px] text-muted-foreground">
                        {formatDay(e.date)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <p className="text-[11px] text-muted-foreground/50">
        Prices via yfinance (delayed). Index membership from public constituent tables. Not
        investment advice.
      </p>
    </div>
  )
}
