/**
 * MarketPage — overall market conditions: index cards, advance/decline breadth,
 * the day's top gainers and losers filtered by market-cap tier, TradingView's
 * live heatmap with index/timeframe pickers, sector performance, macro
 * benchmarks, and one navigable week of earnings and economic releases.
 */

import { useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
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
import { cn, readJSON, writeJSON } from '@/lib/utils'
import { api } from '@/lib/api'
import { peek } from '@/lib/clientCache'
import { K, TTL } from '@/lib/cacheKeys'
import { useCachedResource } from '@/hooks/useCachedResource'
import type {
  CapTier,
  EarningsEvent,
  EarningsSeason,
  EconomicEvent,
  IndexBreadth,
  MarketEvents,
  MarketOverview,
  MarketRow,
  Mover,
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

// Cap tiers mirror CAP_TIERS in services/market_data.py — the bounds live
// server-side, these are only the labels and the order they appear in.
// Shared by the movers screen and the earnings calendar, so "large cap" means
// one thing on the page.
const CAP_TIERS: { value: CapTier; label: string; short: string; noun: string; hint: string }[] = [
  { value: 'all', label: 'All caps', short: 'All', noun: 'companies', hint: 'Every US listing above $300M' },
  { value: 'large', label: 'Large cap', short: 'Large', noun: 'large caps', hint: 'Above $10B' },
  { value: 'mid', label: 'Mid cap', short: 'Mid', noun: 'mid caps', hint: '$2B – $10B' },
  { value: 'small', label: 'Small cap', short: 'Small', noun: 'small caps', hint: '$300M – $2B' },
]

/** Segmented cap-tier picker; `compact` for a card header, full for a section. */
function CapTierToggle({
  value,
  onChange,
  compact = false,
}: {
  value: CapTier
  onChange: (tier: CapTier) => void
  compact?: boolean
}) {
  return (
    <div
      className={cn(
        'flex items-center rounded-lg border border-border bg-card',
        compact ? 'gap-0.5 p-0.5' : 'gap-1 p-1'
      )}
    >
      {CAP_TIERS.map(tier => (
        <button
          key={tier.value}
          onClick={() => onChange(tier.value)}
          title={tier.hint}
          className={cn(
            'rounded font-medium transition-colors',
            compact ? 'h-6 px-2 text-[11px]' : 'h-7 px-2.5 text-xs',
            value === tier.value
              ? 'bg-primary/20 text-primary'
              : 'text-muted-foreground hover:bg-muted hover:text-foreground'
          )}
        >
          {compact ? tier.short : tier.label}
        </button>
      ))}
    </div>
  )
}

const HEATMAP_RANGES = [
  { value: 'change', label: '1D' },
  { value: 'Perf.W', label: '1W' },
  { value: 'Perf.1M', label: '1M' },
  { value: 'Perf.3M', label: '3M' },
  { value: 'Perf.6M', label: '6M' },
  { value: 'Perf.YTD', label: 'YTD' },
  { value: 'Perf.Y', label: '1Y' },
]

// How far the week arrows may travel, matching the API's own `ge=-26, le=26`
// on /market/events/week. One constant, so the arrows, the clamp and the
// server cannot drift apart.
const MAX_WEEK_OFFSET = 26

// Toolbar choices are preferences, not navigation: the filter you always use
// should still be selected tomorrow. Validated on read against the option
// tables above, because storage is user-editable and a value the toolbar does
// not offer would leave a selection nothing can clear.
const MARKET_PREFS_KEY = 'agent-invest-market'
// The week is a cursor rather than a preference. It should survive a tab
// switch and die with the tab — landing on "three weeks ago" tomorrow morning
// would be wrong. That is exactly sessionStorage's lifetime.
const MARKET_SESSION_KEY = 'agent-invest-market-session'

interface MarketPrefs {
  capTier: CapTier
  /** The earnings calendar's own tier — large by default, unlike the movers. */
  earningsCap: CapTier
  index: string
  blockColor: string
}

const DEFAULT_MARKET_PREFS: MarketPrefs = {
  capTier: 'all',
  earningsCap: 'large',
  index: 'SPX500',
  blockColor: 'change',
}

function readMarketPrefs(): MarketPrefs {
  const stored = readJSON<Partial<MarketPrefs>>(MARKET_PREFS_KEY, {})
  return {
    capTier: CAP_TIERS.some(t => t.value === stored.capTier)
      ? (stored.capTier as CapTier)
      : DEFAULT_MARKET_PREFS.capTier,
    earningsCap: CAP_TIERS.some(t => t.value === stored.earningsCap)
      ? (stored.earningsCap as CapTier)
      : DEFAULT_MARKET_PREFS.earningsCap,
    index: INDEX_OPTIONS.some(o => o.value === stored.index)
      ? (stored.index as string)
      : DEFAULT_MARKET_PREFS.index,
    blockColor: HEATMAP_RANGES.some(o => o.value === stored.blockColor)
      ? (stored.blockColor as string)
      : DEFAULT_MARKET_PREFS.blockColor,
  }
}

/** Clamp to the same bound the arrows enforce — storage is not to be trusted. */
function readWeekOffset(): number {
  const { weekOffset } = readJSON(MARKET_SESSION_KEY, { weekOffset: 0 }, sessionStorage)
  if (typeof weekOffset !== 'number' || Number.isNaN(weekOffset)) return 0
  return Math.max(-MAX_WEEK_OFFSET, Math.min(MAX_WEEK_OFFSET, Math.trunc(weekOffset)))
}

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

/** Market cap in the units a person reads it in: $4.9B, $812M. */
function cap(v?: number | null): string {
  if (v == null) return '—'
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`
  return `$${v.toLocaleString('en-US')}`
}

/**
 * One mover. The sector sits next to the ticker rather than in a column of its
 * own — the question it answers is "what kind of company is this", which is
 * part of reading the name, not a separate field to scan down.
 */
function MoverRow({ rank, row, onClick }: { rank: number; row: Mover; onClick: () => void }) {
  const up = (row.change_percent ?? 0) > 0
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-3 border-b border-border/40 py-2 text-left transition-colors last:border-b-0 hover:bg-muted/40"
    >
      <span className="w-4 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground/50">
        {rank}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[13px] font-semibold text-foreground">{row.symbol}</span>
          <Badge
            variant="secondary"
            className="shrink-0 px-1.5 py-0 text-[9px] font-medium uppercase tracking-wide"
          >
            {row.sector ?? 'Unclassified'}
          </Badge>
        </div>
        <p className="truncate text-[11px] text-muted-foreground/70">{row.name}</p>
      </div>

      <div className="shrink-0 text-right">
        <p className="text-[13px] font-medium tabular-nums text-foreground">{num(row.last)}</p>
        <p className="text-[10px] tabular-nums text-muted-foreground/60">{cap(row.market_cap)}</p>
      </div>

      <span
        className={cn(
          'w-[68px] shrink-0 text-right text-[13px] font-semibold tabular-nums',
          up ? 'text-emerald-400' : 'text-rose-400'
        )}
      >
        {pct(row.change_percent)}
      </span>
    </button>
  )
}

function MoversCard({
  title,
  rows,
  loading,
  gaining,
  onSelect,
}: {
  title: string
  rows: Mover[]
  loading: boolean
  gaining: boolean
  onSelect: (symbol: string) => void
}) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-2 py-4">
        <div className="flex items-center gap-2">
          {gaining ? (
            <TrendingUp size={14} className="text-emerald-400" />
          ) : (
            <TrendingDown size={14} className="text-rose-400" />
          )}
          <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        </div>
        <Separator />

        {loading && rows.length === 0 ? (
          <div className="flex items-center gap-2 py-10 text-xs text-muted-foreground">
            <Loader2 size={14} className="animate-spin" />
            Screening the tape…
          </div>
        ) : rows.length === 0 ? (
          <p className="py-10 text-center text-xs text-muted-foreground/60">
            Nothing in this tier right now.
          </p>
        ) : (
          <div className="flex flex-col">
            {rows.map((row, i) => (
              <MoverRow
                key={row.symbol}
                rank={i + 1}
                row={row}
                onClick={() => onSelect(row.symbol)}
              />
            ))}
          </div>
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

/**
 * One company's report inside a day column.
 *
 * A week that has already happened shows what was actually reported and how far
 * it landed from the estimate; a week still ahead shows the estimate alone.
 */
function EarningsTile({
  event,
  onSelect,
}: {
  event: EarningsEvent
  onSelect: (symbol: string) => void
}) {
  const reported = event.reported_eps != null
  const surprise = event.surprise_percent
  const session =
    event.session === 'before_open' ? 'before open' : event.session === 'after_close' ? 'after close' : null

  const detail = reported
    ? `${event.reported_eps!.toFixed(2)}${event.eps_estimate != null ? ` vs ${event.eps_estimate.toFixed(2)}` : ''}`
    : event.eps_estimate != null
      ? `est. ${event.eps_estimate.toFixed(2)}`
      : null

  return (
    <button
      onClick={() => onSelect(event.symbol)}
      title={[
        event.name ? `${event.symbol} — ${event.name}` : event.symbol,
        event.market_cap != null ? `${cap(event.market_cap)} market cap` : null,
        session,
        reported ? `reported ${event.reported_eps!.toFixed(2)}` : null,
        event.eps_estimate != null ? `est. ${event.eps_estimate.toFixed(2)}` : null,
        surprise != null ? `surprise ${pct(surprise)}` : null,
      ]
        .filter(Boolean)
        .join(' · ')}
      className="flex flex-col rounded bg-card px-1.5 py-1 text-left transition-colors hover:bg-muted"
    >
      {/* Which session it lands in is the group heading above, not repeated here. */}
      <span className="font-mono text-[11px] font-semibold text-foreground">{event.symbol}</span>

      {detail && (
        <span className="text-[9px] tabular-nums text-muted-foreground/70">{detail}</span>
      )}
      {surprise != null && (
        <span className={cn('text-[9px] tabular-nums', tone(surprise))}>{pct(surprise)} surp.</span>
      )}
    </button>
  )
}

/** Reports shown per day before the column folds the rest behind "+N more". */
const EARNINGS_PER_DAY = 8

/**
 * A day's reports split by the session they land in, in trading order: the
 * open reacts to the first group, the next morning to the second. Companies
 * that have not given a time come last rather than being guessed into either.
 */
const SESSION_GROUPS: { session: EarningsEvent['session']; label: string }[] = [
  { session: 'before_open', label: 'Before open' },
  { session: 'after_close', label: 'After close' },
  { session: null, label: 'Time TBA' },
]

function sessionGroup(e: EarningsEvent): EarningsEvent['session'] {
  return e.session === 'before_open' || e.session === 'after_close' ? e.session : null
}

/**
 * Mon–Fri style calendar: one column per trading day, tickers stacked inside.
 *
 * In peak season a single day can carry eighty large-cap reports, so each
 * column shows its biggest names (the backend sends them largest first) and
 * folds the rest behind a toggle rather than stretching the whole row.
 */
function EarningsWeek({
  earnings,
  startIso,
  todayIso,
  onSelect,
}: {
  earnings: EarningsEvent[]
  startIso: string
  todayIso: string
  onSelect: (symbol: string) => void
}) {
  const today = todayIso
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  // A different week is a different set of columns; start folded again.
  useEffect(() => setExpanded(new Set()), [startIso])

  const toggle = (day: string) =>
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(day)) next.delete(day)
      else next.add(day)
      return next
    })

  const byDay = new Map<string, EarningsEvent[]>()
  for (const e of earnings) {
    const list = byDay.get(e.date) ?? []
    list.push(e)
    byDay.set(e.date, list)
  }

  // Mon–Fri always; Saturday or Sunday only when someone reports on one
  // (Berkshire does, every quarter).
  const days = businessDays(startIso, 5)
  for (const weekend of [1, 2]) {
    const day = new Date(days[4])
    day.setDate(day.getDate() + weekend)
    if (byDay.has(isoDay(day))) days.push(day)
  }
  const columns = ['grid-cols-5', 'grid-cols-6', 'grid-cols-7'][days.length - 5]

  return (
    <div className={cn('grid gap-1.5', columns)}>
      {days.map(day => {
        const key = isoDay(day)
        const items = byDay.get(key) ?? []
        const isToday = key === today
        const open = expanded.has(key)
        const hidden = items.length - EARNINGS_PER_DAY
        // Fold first, group second: folded, a day still shows its biggest
        // names whichever session they report in. Items arrive largest first.
        const shown = open || hidden <= 0 ? items : items.slice(0, EARNINGS_PER_DAY)
        const groups = SESSION_GROUPS.map(g => ({
          ...g,
          total: items.filter(e => sessionGroup(e) === g.session).length,
          events: shown.filter(e => sessionGroup(e) === g.session),
        })).filter(g => g.events.length > 0)

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
                {items.length > 0 && (
                  <span className="ml-1 text-muted-foreground/40">· {items.length}</span>
                )}
              </span>
            </div>

            <div className="flex flex-1 flex-col gap-1 p-1.5">
              {items.length === 0 ? (
                <span className="mt-3 text-center text-[10px] text-muted-foreground/30">—</span>
              ) : (
                groups.map(g => (
                  <div key={g.label} className="flex flex-col gap-1">
                    <span className="mt-0.5 flex items-baseline justify-between px-0.5 text-[8px] uppercase tracking-wide text-muted-foreground/50">
                      {g.label}
                      <span className="tabular-nums">{g.total}</span>
                    </span>
                    {g.events.map(e => (
                      <EarningsTile key={e.symbol} event={e} onSelect={onSelect} />
                    ))}
                  </div>
                ))
              )}
              {hidden > 0 && (
                <button
                  onClick={() => toggle(key)}
                  className="mt-0.5 rounded px-1.5 py-1 text-[10px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  {open ? 'Show fewer' : `+${hidden} more`}
                </button>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/**
 * How far through the reporting season the market is: "Q3 2026 season ·
 * 703 / 863 large caps reported". The total is every US-listed company in
 * the chosen cap tier reporting in the quarter the viewed week falls in, so it
 * follows both the week picker and the tier.
 */
function SeasonProgress({ season, cap }: { season: EarningsSeason; cap: CapTier }) {
  const { label, total, reported } = season
  const tier = CAP_TIERS.find(t => t.value === cap) ?? CAP_TIERS[1]
  const window = [season.start, season.end]
    .map(d => new Date(`${d}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }))
    .join(' – ')

  return (
    <span
      title={`${reported} of ${total} US-listed companies (${tier.hint}) reporting ${window} are in`}
      className="flex items-center gap-1.5 text-[11px] font-normal text-muted-foreground"
    >
      <span className="tabular-nums">
        {label} season · <span className="text-foreground">{reported}</span> / {total} {tier.noun} reported
      </span>
      <span className="h-1 w-14 overflow-hidden rounded-full bg-muted">
        <span
          className="block h-full rounded-full bg-primary/70"
          style={{ width: `${total ? (reported / total) * 100 : 0}%` }}
        />
      </span>
    </span>
  )
}

/** Label, actual, forecast, previous — shared by the header and every row. */
const FIGURE_GRID = 'grid grid-cols-[minmax(0,1fr)_4.25rem_5.25rem_4.25rem] items-baseline gap-x-2'

/** "0.3%", "165K", "-105.0B", "52.4" — the release's own way of quoting it. */
function figure(value: number | null, unit: string, decimals: number): string {
  if (value == null) return '—'
  return `${value.toFixed(decimals)}${unit}`
}

/**
 * One release: its name and day, then a line per headline figure.
 *
 * The forecast says where it came from. "cons." is economists' consensus; "avg"
 * is the mean of the three readings before, standing in where no consensus is
 * available — a baseline, not a prediction anyone made.
 */
function ReleaseRow({ event, isPast }: { event: EconomicEvent; isPast: boolean }) {
  const figures = event.indicators ?? []

  return (
    <div className="flex flex-col gap-1 border-b border-border/40 py-2 last:border-b-0">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className={cn(
              'h-1.5 w-1.5 shrink-0 rounded-full',
              event.importance === 'high' ? 'bg-amber-400' : 'bg-muted-foreground/40'
            )}
          />
          <span className="truncate text-xs text-foreground" title={event.release}>
            {event.name}
          </span>
        </div>
        <span className="shrink-0 text-[11px] text-muted-foreground">{formatDay(event.date)}</span>
      </div>

      {figures.map(f => (
        <div key={f.label} className={cn(FIGURE_GRID, 'pl-3.5 text-[11px] tabular-nums')}>
          <span className="truncate text-muted-foreground" title={f.period ? `${f.label} · ${f.period}` : f.label}>
            {f.label}
            {f.period && <span className="ml-1 text-[10px] text-muted-foreground/50">{f.period}</span>}
          </span>
          <span
            className={cn('text-right', f.actual != null ? 'font-semibold text-foreground' : 'text-muted-foreground/40')}
            title={f.actual != null ? undefined : isPast ? 'Not on FRED yet' : 'Not released yet'}
          >
            {figure(f.actual, f.unit, f.decimals)}
          </span>
          <span
            className="text-right text-muted-foreground"
            title={
              f.forecast_source === 'consensus'
                ? "Economists' consensus (ForexFactory)"
                : f.forecast_source === 'average'
                  ? 'Mean of the three prior readings — a baseline, not a consensus forecast'
                  : undefined
            }
          >
            {figure(f.forecast, f.unit, f.decimals)}
            {f.forecast_source && (
              <span className="ml-1 text-[8px] uppercase tracking-wide text-muted-foreground/50">
                {f.forecast_source === 'consensus' ? 'cons.' : 'avg'}
              </span>
            )}
          </span>
          <span className="text-right text-muted-foreground">{figure(f.previous, f.unit, f.decimals)}</span>
        </div>
      ))}
    </div>
  )
}

/** "Last week", "in 3 weeks" — the offset in words, so the dates have context. */
function relativeWeek(offset: number): string {
  if (offset === 0) return 'This week'
  if (offset === -1) return 'Last week'
  if (offset === 1) return 'Next week'
  return offset < 0 ? `${-offset} weeks ago` : `in ${offset} weeks`
}

function WeekPicker({
  offset,
  onChange,
  events,
}: {
  offset: number
  onChange: Dispatch<SetStateAction<number>>
  events: MarketEvents | null
}) {
  // Stepping off the rendered `offset` would collapse two quick clicks into
  // one week — both would read the same pre-render value.
  const step = (delta: number) =>
    onChange(prev => Math.max(-MAX_WEEK_OFFSET, Math.min(MAX_WEEK_OFFSET, prev + delta)))

  return (
    <div className="flex items-center gap-1 rounded-lg border border-border bg-card p-1">
      <button
        onClick={() => step(-1)}
        disabled={offset <= -MAX_WEEK_OFFSET}
        title="Previous week"
        className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-30 disabled:hover:bg-transparent"
      >
        <ChevronLeft size={15} />
      </button>

      <div className="min-w-[168px] px-1 text-center">
        <p className="text-xs font-medium leading-tight text-foreground">
          {events ? `${formatDay(events.week_start)} – ${formatDay(events.week_end)}` : '—'}
        </p>
        <p className="text-[10px] leading-tight text-muted-foreground/60">
          {relativeWeek(offset)}
        </p>
      </div>

      <button
        onClick={() => step(1)}
        disabled={offset >= MAX_WEEK_OFFSET}
        title="Next week"
        className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-30 disabled:hover:bg-transparent"
      >
        <ChevronRight size={15} />
      </button>

      {offset !== 0 && (
        <button
          onClick={() => onChange(0)}
          className="ml-1 h-7 rounded px-2 text-[11px] font-medium text-primary transition-colors hover:bg-primary/10"
        >
          Today
        </button>
      )}
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

  const [initialPrefs] = useState(readMarketPrefs)
  const [index, setIndex] = useState(initialPrefs.index)
  const [blockColor, setBlockColor] = useState(initialPrefs.blockColor)
  const [capTier, setCapTier] = useState<CapTier>(initialPrefs.capTier)
  const [earningsCap, setEarningsCap] = useState<CapTier>(initialPrefs.earningsCap)
  const [weekOffset, setWeekOffset] = useState(readWeekOffset)

  useEffect(() => {
    writeJSON(MARKET_PREFS_KEY, { capTier, earningsCap, index, blockColor })
  }, [capTier, earningsCap, index, blockColor])

  useEffect(() => {
    writeJSON(MARKET_SESSION_KEY, { weekOffset }, sessionStorage)
  }, [weekOffset])

  // Overview — the merge carrying a dropped symbol forward happens inside the
  // fetcher rather than in a setState, so the carried-forward value is what
  // lands in the cache and survives leaving the page.
  const overviewRes = useCachedResource(
    K.overview(),
    () =>
      api.market
        .overview()
        .then(next => mergeOverview(peek<MarketOverview>(K.overview()).value ?? null, next)),
    { ttl: TTL.overview, refreshIntervalMs: 30_000 },
  )
  const overview = overviewRes.data ?? null
  const overviewError = overviewRes.error?.message ?? overview?.error ?? null

  // Breadth prices ~700 constituents and takes minutes, so it is polled on its
  // own far slower clock. At 60s the previous request had not returned before
  // the next went out, leaving two or three permanently in flight and eating
  // the browser's per-origin connection budget. Single-flight now caps it at
  // one; this interval keeps it that way.
  const breadthRes = useCachedResource(
    K.breadth(),
    () => api.market.breadth(),
    {
      ttl: TTL.breadth,
      refreshIntervalMs: 600_000,
      shouldCache: b => b.indices.length > 0,
    },
  )
  const indexBreadth = breadthRes.data ?? null

  // Keyed per tier, so switching the filter back to one already fetched is
  // instant — the job the component-local map used to do, except this one
  // outlives the page.
  const moversRes = useCachedResource(
    K.movers(capTier),
    () => api.market.movers(capTier, 10),
    { ttl: TTL.movers, refreshIntervalMs: 60_000 },
  )

  const eventsRes = useCachedResource(
    K.weekEvents(weekOffset, earningsCap),
    () => api.market.weekEvents(weekOffset, earningsCap),
    { ttl: TTL.weekEvents },
  )
  const events = eventsRes.data ?? null

  const breadth = overview?.breadth
  const earnings = events?.earnings ?? []
  const economic = events?.economic
  const tierMovers = moversRes.data
  const activeTier = CAP_TIERS.find(t => t.value === capTier)

  return (
    <div className="max-w-[1500px] mx-auto px-6 py-6 flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Market Overview</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Index conditions, the day's biggest movers, sector rotation and the week's events.
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
      {overviewRes.isLoading ? (
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

        {breadthRes.isLoading ? (
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 size={16} className="animate-spin" />
            Counting advancers and decliners…
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {(indexBreadth?.indices ?? []).map(row => (
              <BreadthCard key={row.index} row={row} />
            ))}
          </div>
        )}
      </div>

      {/* Top gainers and losers */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Top movers
            </h2>
            <p className="mt-0.5 text-[11px] text-muted-foreground/60">
              Biggest percentage moves today · {activeTier?.hint}
            </p>
          </div>

          <CapTierToggle value={capTier} onChange={setCapTier} />
        </div>

        {tierMovers?.error && (
          <Alert variant="destructive">
            <AlertCircle size={16} />
            <AlertDescription>{tierMovers.error}</AlertDescription>
          </Alert>
        )}

        <div className="grid gap-4 lg:grid-cols-2">
          <MoversCard
            title="Top 10 gainers"
            rows={tierMovers?.gainers ?? []}
            loading={moversRes.isLoading}
            gaining
            onSelect={symbol => navigate(`/charting?ticker=${encodeURIComponent(symbol)}`)}
          />
          <MoversCard
            title="Top 10 losers"
            rows={tierMovers?.losers ?? []}
            loading={moversRes.isLoading}
            gaining={false}
            onSelect={symbol => navigate(`/charting?ticker=${encodeURIComponent(symbol)}`)}
          />
        </div>
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

      {/* Economic events */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            <CalendarDays size={14} />
            Economic events
          </h2>
          <WeekPicker offset={weekOffset} onChange={setWeekOffset} events={events} />
        </div>

        <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
          {/* Earnings — laid out as a working-week calendar */}
          <Card>
            <CardContent className="flex flex-col gap-3 py-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="flex flex-col gap-1">
                  <h3 className="text-sm font-semibold text-foreground">
                    Earnings <span className="text-muted-foreground/60">({earnings.length})</span>
                  </h3>
                  {events?.season && <SeasonProgress season={events.season} cap={earningsCap} />}
                </div>
                <CapTierToggle value={earningsCap} onChange={setEarningsCap} compact />
              </div>
              <Separator />

              {eventsRes.isLoading ? (
                <div className="flex items-center gap-2 py-6 text-xs text-muted-foreground">
                  <Loader2 size={14} className="animate-spin" />
                  Loading {CAP_TIERS.find(t => t.value === earningsCap)?.noun ?? 'earnings'}…
                </div>
              ) : (
                <EarningsWeek
                  earnings={earnings}
                  startIso={events?.week_start ?? isoDay(new Date())}
                  todayIso={events?.today ?? isoDay(new Date())}
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

              {eventsRes.isLoading ? (
                <div className="flex items-center gap-2 py-6 text-xs text-muted-foreground">
                  <Loader2 size={14} className="animate-spin" />
                  Loading calendar…
                </div>
              ) : economic?.error ? (
                // The key is set but FRED did not answer — not a setup problem.
                <div className="py-6 text-center">
                  <p className="text-xs text-muted-foreground">Couldn't reach FRED</p>
                  <p className="mt-1 text-[11px] text-muted-foreground/60">
                    The St. Louis Fed's release calendar did not respond. It will retry on the
                    next load.
                  </p>
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
                  No major releases {relativeWeek(weekOffset).toLowerCase()}.
                </p>
              ) : (
                <div className="flex flex-col">
                  {economic.events.some(e => e.indicators?.length) && (
                    <div className={cn(FIGURE_GRID, 'pb-1 text-[9px] uppercase tracking-wide text-muted-foreground/50')}>
                      <span />
                      <span className="text-right">Actual</span>
                      <span className="text-right">Forecast</span>
                      <span className="text-right">Previous</span>
                    </div>
                  )}
                  {economic.events.map(e => (
                    <ReleaseRow
                      key={`${e.name}-${e.date}`}
                      event={e}
                      isPast={e.date < (events?.today ?? isoDay(new Date()))}
                    />
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
