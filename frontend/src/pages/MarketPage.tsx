/**
 * MarketPage — overall market conditions: index cards, advance/decline breadth,
 * TradingView's live heatmap with index/timeframe pickers, sector performance,
 * macro benchmarks, and the week's earnings and economic releases.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  CalendarDays,
  ExternalLink,
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
import type {
  EarningsEvent,
  IndexBreadth,
  MarketBreadth,
  MarketEvents,
  MarketOverview,
  MarketRow,
} from '@/lib/types'
import TradingViewHeatmap from '@/components/market/TradingViewHeatmap'

// TradingView heatmap identifiers — `dataSource` picks the universe,
// `blockColor` the performance window used to colour each tile.
const INDEX_OPTIONS = [
  { value: 'SPX500', label: 'S&P 500' },
  { value: 'NASDAQ100', label: 'Nasdaq 100' },
  { value: 'DJDJI', label: 'Dow Jones 30' },
  { value: 'AllUSA', label: 'All US' },
]

const HEATMAP_RANGES = [
  { value: 'change', label: '1D' },
  { value: 'Perf.W', label: '1W' },
  { value: 'Perf.1M', label: '1M' },
  { value: 'Perf.3M', label: '3M' },
  { value: 'Perf.6M', label: '6M' },
  { value: 'Perf.YTD', label: 'YTD' },
  { value: 'Perf.Y', label: '1Y' },
]

/** The same view on tradingview.com, so the widget can be opened full size. */
function heatmapSourceUrl(dataSource: string, blockColor: string): string {
  const params = new URLSearchParams({
    color: blockColor,
    dataset: dataSource,
    group: 'sector',
    size: 'market_cap_basic',
  })
  return `https://www.tradingview.com/heatmap/stock/?${params}`
}

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

/**
 * Advance/decline for one index: a stacked up/flat/down bar with the split
 * spelled out, so "index is green but most of its stocks are red" is visible.
 */
function BreadthCard({ row }: { row: IndexBreadth }) {
  const up = row.advancing_percent ?? 0
  const down = row.declining_percent ?? 0
  const flat = row.unchanged_percent ?? 0
  const hasData = (row.counted ?? 0) > 0

  return (
    <Card>
      <CardContent className="flex flex-col gap-2.5 py-4">
        <div className="flex items-baseline justify-between gap-2">
          <p className="text-[13px] font-semibold text-foreground">{row.label}</p>
          <span className="text-[10px] text-muted-foreground/60">
            {hasData ? `${row.counted} stocks` : '—'}
          </span>
        </div>

        {hasData ? (
          <>
            <div className="flex items-end justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xl font-bold leading-none tabular-nums text-rose-400">
                  {down.toFixed(1)}%
                </p>
                <p className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground/60">
                  down
                </p>
              </div>
              <div className="min-w-0 text-right">
                <p className="text-xl font-bold leading-none tabular-nums text-emerald-400">
                  {up.toFixed(1)}%
                </p>
                <p className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground/60">
                  up
                </p>
              </div>
            </div>

            {/* Decline / unchanged / advance, mirroring the figures above it */}
            <div className="flex h-2.5 overflow-hidden rounded-full bg-muted">
              <div className="bg-rose-500" style={{ width: `${down}%` }} />
              <div className="bg-muted-foreground/40" style={{ width: `${flat}%` }} />
              <div className="bg-emerald-500" style={{ width: `${up}%` }} />
            </div>

            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
              <span className="tabular-nums">
                {row.declining} down · {row.advancing} up
                {row.unchanged ? ` · ${row.unchanged} flat` : ''}
              </span>
              <span className={cn('shrink-0 tabular-nums', tone(row.median_change_percent))}>
                median {pct(row.median_change_percent)}
              </span>
            </div>
          </>
        ) : (
          <p className="py-3 text-xs text-muted-foreground/60">{row.error ?? 'No data'}</p>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * The next `count` trading days from `startIso` (weekends skipped).
 *
 * The start comes from the API, which anchors to US market time — deriving it
 * from the browser clock would put the columns a day off for anyone whose
 * local date differs from New York's.
 */
function businessDays(startIso: string, count = 5): Date[] {
  const [y, m, d] = startIso.split('-').map(Number)
  const cursor = y && m && d ? new Date(y, m - 1, d) : new Date()
  cursor.setHours(0, 0, 0, 0)

  const days: Date[] = []
  while (days.length < count) {
    const weekday = cursor.getDay()
    if (weekday !== 0 && weekday !== 6) days.push(new Date(cursor))
    cursor.setDate(cursor.getDate() + 1)
  }
  return days
}

function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** Mon–Fri style calendar: one column per trading day, tickers stacked inside. */
function EarningsWeek({
  earnings,
  startIso,
  onSelect,
}: {
  earnings: EarningsEvent[]
  startIso: string
  onSelect: (symbol: string) => void
}) {
  const days = businessDays(startIso, 5)
  const today = startIso

  const byDay = new Map<string, EarningsEvent[]>()
  for (const e of earnings) {
    const list = byDay.get(e.date) ?? []
    list.push(e)
    byDay.set(e.date, list)
  }

  return (
    <div className="grid grid-cols-5 gap-1.5">
      {days.map(day => {
        const key = isoDay(day)
        const items = byDay.get(key) ?? []
        const isToday = key === today

        return (
          <div
            key={key}
            className={cn(
              'flex min-h-[132px] flex-col rounded-md border',
              isToday ? 'border-primary/40 bg-primary/5' : 'border-border/60 bg-muted/20'
            )}
          >
            <div
              className={cn(
                'flex items-baseline justify-between gap-1 border-b px-2 py-1.5',
                isToday ? 'border-primary/30' : 'border-border/50'
              )}
            >
              <span
                className={cn(
                  'text-[10px] font-semibold uppercase tracking-wide',
                  isToday ? 'text-primary' : 'text-muted-foreground'
                )}
              >
                {day.toLocaleDateString('en-US', { weekday: 'short' })}
              </span>
              <span className="text-[10px] tabular-nums text-muted-foreground/60">
                {day.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric' })}
              </span>
            </div>

            <div className="flex flex-1 flex-col gap-1 p-1.5">
              {items.length === 0 ? (
                <span className="mt-3 text-center text-[10px] text-muted-foreground/30">—</span>
              ) : (
                items.map(e => (
                  <button
                    key={e.symbol}
                    onClick={() => onSelect(e.symbol)}
                    title={
                      e.eps_estimate != null
                        ? `${e.symbol} · EPS est. ${e.eps_estimate.toFixed(2)}`
                        : e.symbol
                    }
                    className="flex flex-col rounded bg-card px-1.5 py-1 text-left transition-colors hover:bg-muted"
                  >
                    <span className="font-mono text-[11px] font-semibold text-foreground">
                      {e.symbol}
                    </span>
                    {e.eps_estimate != null && (
                      <span className="text-[9px] tabular-nums text-muted-foreground/70">
                        est. {e.eps_estimate.toFixed(2)}
                      </span>
                    )}
                  </button>
                ))
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
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

  const [index, setIndex] = useState('SPX500')
  const [blockColor, setBlockColor] = useState('change')

  const [indexBreadth, setIndexBreadth] = useState<MarketBreadth | null>(null)
  const [loadingBreadth, setLoadingBreadth] = useState(true)

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

  // Advance/decline — one full constituent scan, so poll gently
  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const data = await api.market.breadth()
        if (!cancelled && data.indices.length) setIndexBreadth(data)
      } catch {
        /* keep whatever we already showed */
      } finally {
        if (!cancelled) setLoadingBreadth(false)
      }
    }

    load()
    const id = setInterval(load, 60000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

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

      {/* Advance / decline breadth */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Advancers vs decliners
          </h2>
          <span className="text-[11px] text-muted-foreground/60">
            Share of each index's constituents up or down on the day
          </span>
        </div>

        {loadingBreadth && !indexBreadth ? (
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 size={16} className="animate-spin" />
            Counting advancers and decliners…
          </div>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-4">
              {(indexBreadth?.indices ?? []).map(row => (
                <BreadthCard key={row.index} row={row} />
              ))}
            </div>
            <p className="text-[10px] text-muted-foreground/50">
              Small caps use the S&P 600 — Russell 2000 membership isn't published by any free
              source we can read.
            </p>
          </>
        )}
      </div>

      {/* Heatmap */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Heatmap
            <a
              href={heatmapSourceUrl(index, blockColor)}
              target="_blank"
              rel="noreferrer noopener"
              title="Open this heatmap on TradingView"
              className="flex items-center gap-1 text-[11px] font-medium normal-case tracking-normal text-muted-foreground/60 transition-colors hover:text-primary"
            >
              TradingView
              <ExternalLink size={11} />
            </a>
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
              {HEATMAP_RANGES.map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setBlockColor(opt.value)}
                  className={cn(
                    'h-7 rounded px-2 text-xs font-medium transition-colors',
                    blockColor === opt.value
                      ? 'bg-primary/20 text-primary'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <Card>
          <CardContent className="p-3">
            <TradingViewHeatmap dataSource={index} blockColor={blockColor} height={620} />
            <p className="mt-1 px-1 text-[10px] text-muted-foreground/60">
              Live heatmap by TradingView · tiles sized by market cap, grouped by sector
            </p>
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
              {formatDay(events.week_start)} → {formatDay(events.week_end)}
            </span>
          )}
        </h2>

        <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
          {/* Earnings — laid out as a working-week calendar */}
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
              ) : (
                <EarningsWeek
                  earnings={earnings}
                  startIso={events?.week_start ?? isoDay(new Date())}
                  onSelect={symbol => navigate(`/charting?ticker=${symbol}`)}
                />
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
