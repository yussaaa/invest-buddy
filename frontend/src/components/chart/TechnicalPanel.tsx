/**
 * TechnicalPanel — RSI, MACD and the 5/20/50/200/250 moving-average ladder for the
 * charted symbol, with an on-demand plain-English read from the fast model.
 *
 * The explanation is behind a button rather than automatic: it costs a model
 * call per symbol, and most of the time the numbers speak for themselves.
 */

import { Fragment, useEffect, useState } from 'react'
import { AlertCircle, Loader2, Sparkles } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { cn, readJSON, writeJSON } from '@/lib/utils'
import { api } from '@/lib/api'
import { cached, put } from '@/lib/clientCache'
import { K, TTL } from '@/lib/cacheKeys'
import { useCachedResource, useCachedValue } from '@/hooks/useCachedResource'
import type {
  DrawdownProfile,
  DrawdownStatus,
  MaLevel,
  TechnicalsExplanation,
} from '@/lib/types'

function fmt(v?: number | null, digits = 2): string {
  if (v == null) return '—'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function signed(v?: number | null, digits = 2): string {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${fmt(v, digits)}`
}

/**
 * Decimals for a MACD reading: two while the whole part is a single digit,
 * one beyond. MACD is in price units, so it runs from ±0.05 on a $20 stock to
 * ±40 on a $2,000 one — a fixed three decimals was noise at one end and
 * clutter at the other.
 */
function macdDigits(v?: number | null): number {
  return v != null && Math.abs(v) >= 10 ? 1 : 2
}

function tone(v?: number | null) {
  if (v == null || v === 0) return 'text-muted-foreground'
  return v > 0 ? 'text-emerald-400' : 'text-rose-400'
}

/** A decimal rate as a signed percentage: 0.052 → "+5.2%". */
function pctSigned(v?: number | null, digits = 1): string {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`
}

/** Share volume, abbreviated: 1_540_000 → "1.5M". */
function compactVol(v?: number | null): string {
  if (v == null) return '—'
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`
  return String(v)
}

// One-tap macro proxies for the relative-strength comparison, plus whatever
// ticker the reader types. The chosen set persists — it's a property of how the
// reader likes to compare, not of the symbol on screen.
const RS_PRESETS = [
  { sym: 'GLD', label: 'Gold' },
  { sym: 'USO', label: 'Oil' },
  { sym: 'TLT', label: 'Bonds' },
  { sym: 'UUP', label: 'Dollar' },
  { sym: 'IBIT', label: 'Bitcoin' },
]
const RS_COMPARE_KEY = 'agent-invest.charting.rs-compare'
const RS_MAX_COMPARE = 5
const RS_TICKER = /^[A-Z0-9.\-^=]{1,12}$/

function readCompare(): string[] {
  const raw = readJSON<unknown>(RS_COMPARE_KEY, [])
  if (!Array.isArray(raw)) return []
  return raw.filter((s): s is string => typeof s === 'string').slice(0, RS_MAX_COMPARE)
}

/** 0–100 track with the oversold/overbought bands marked. */
function RsiGauge({ value }: { value: number }) {
  return (
    <div className="relative h-2 w-full rounded-full bg-muted">
      <div className="absolute left-0 top-0 h-full w-[30%] rounded-l-full bg-emerald-500/20" />
      <div className="absolute right-0 top-0 h-full w-[30%] rounded-r-full bg-rose-500/20" />
      <div
        className="absolute top-1/2 h-3 w-[3px] -translate-y-1/2 rounded-full bg-foreground"
        style={{ left: `calc(${Math.max(0, Math.min(100, value))}% - 1.5px)` }}
      />
    </div>
  )
}

/**
 * The recent RSI path on the same fixed 0–100 scale as the gauge, with the 30
 * and 70 bands drawn in. Fixed rather than auto-scaled on purpose: a series
 * hovering between 45 and 55 should look flat, not like a swing to the bands.
 */
function RsiSparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length < 2) return null

  const W = 100
  const H = 40
  const y = (v: number) => H - (Math.max(0, Math.min(100, v)) / 100) * H
  const x = (i: number) => (i / (values.length - 1)) * W
  const points = values.map((v, i) => `${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ')

  return (
    <div className="flex flex-col gap-0.5">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="h-10 w-full"
        role="img"
        aria-label={`RSI over the last ${values.length} sessions`}
      >
        <rect x={0} y={0} width={W} height={y(70)} className="fill-rose-500/10" />
        <rect x={0} y={y(30)} width={W} height={H - y(30)} className="fill-emerald-500/10" />
        <line
          x1={0}
          x2={W}
          y1={y(50)}
          y2={y(50)}
          className="stroke-border"
          strokeDasharray="2 2"
          vectorEffect="non-scaling-stroke"
        />
        <polyline
          points={points}
          fill="none"
          className={cn('stroke-current', className)}
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="flex justify-between text-[9px] text-muted-foreground/50">
        <span>{values.length} sessions</span>
        <span>today</span>
      </div>
    </div>
  )
}

/**
 * The recent MACD histogram as bars either side of zero. Unlike RSI there is
 * no fixed scale — the histogram is in price units — so the bars are scaled to
 * the largest one shown, symmetrically, which keeps zero in the middle and
 * makes shrinking bars read as fading momentum.
 */
function MacdHistogramBars({ values }: { values: number[] }) {
  if (values.length < 2) return null

  const W = 100
  const H = 40
  const mid = H / 2
  const peak = Math.max(...values.map(Math.abs)) || 1
  const slot = W / values.length
  const barW = slot * 0.7

  return (
    <div className="flex flex-col gap-0.5">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="h-10 w-full"
        role="img"
        aria-label={`MACD histogram over the last ${values.length} sessions`}
      >
        <line
          x1={0}
          x2={W}
          y1={mid}
          y2={mid}
          className="stroke-border"
          vectorEffect="non-scaling-stroke"
        />
        {values.map((v, i) => {
          const h = (Math.abs(v) / peak) * (mid - 1)
          return (
            <rect
              key={i}
              x={i * slot + (slot - barW) / 2}
              y={v >= 0 ? mid - h : mid}
              width={barW}
              height={Math.max(h, 0.5)}
              className={v >= 0 ? 'fill-emerald-500/70' : 'fill-rose-500/70'}
            >
              <title>{signed(v, macdDigits(v))}</title>
            </rect>
          )
        })}
      </svg>
      <div className="flex justify-between text-[9px] text-muted-foreground/50">
        <span>{values.length} sessions</span>
        <span>today</span>
      </div>
    </div>
  )
}

const DRAWDOWN_LABELS: Record<DrawdownStatus, string> = {
  at_high: 'At high',
  pullback: 'Pullback',
  correction: 'Correction',
  bear_market: 'Bear market',
}

/** Emerald near the high through to rose in a bear market. */
const DRAWDOWN_TONE: Record<DrawdownStatus, { text: string; fill: string }> = {
  at_high: { text: 'text-emerald-400', fill: 'bg-emerald-500' },
  pullback: { text: 'text-foreground', fill: 'bg-sky-500' },
  correction: { text: 'text-amber-400', fill: 'bg-amber-500' },
  bear_market: { text: 'text-rose-400', fill: 'bg-rose-500' },
}

/**
 * Where price sits between its 52-week low and high, with the correction and
 * bear-market levels drawn on the same scale so the thresholds are visible
 * rather than left as arithmetic for the reader.
 */
function RangeBar({ profile }: { profile: DrawdownProfile }) {
  const { low_52w: low, high_52w: high, range_position: position } = profile
  if (low == null || high == null || position == null) return null

  const span = high - low
  // A halted or single-bar symbol has no range to place anything within.
  if (span <= 0) {
    return <p className="py-1 text-xs text-muted-foreground/60">No range to show</p>
  }

  // The thresholds are levels measured down from the high; convert each to its
  // position on the low→high track. They fall off the left end when the range
  // is narrower than the threshold, in which case there is nothing to draw.
  const tickAt = (fraction: number) => ((high * fraction - low) / span) * 100
  const ticks = [
    { label: '−20%', pct: tickAt(0.8) },
    { label: '−10%', pct: tickAt(0.9) },
  ].filter(t => t.pct > 2 && t.pct < 98)

  const fill = DRAWDOWN_TONE[profile.status ?? 'pullback'].fill

  return (
    <div className="flex flex-col gap-1">
      <div className="relative h-2 w-full rounded-full bg-muted">
        <div
          className={cn('absolute left-0 top-0 h-full rounded-l-full opacity-40', fill)}
          style={{ width: `${position}%` }}
        />
        {ticks.map(t => (
          <div
            key={t.label}
            title={`${t.label} from the 52-week high`}
            className="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-muted-foreground/50"
            style={{ left: `${t.pct}%` }}
          />
        ))}
        <div
          data-testid="range-marker"
          className="absolute top-1/2 h-3.5 w-[3px] -translate-y-1/2 rounded-full bg-foreground"
          style={{ left: `calc(${position}% - 1.5px)` }}
        />
      </div>

      <div className="relative h-3 text-[9px] text-muted-foreground/50">
        <span className="absolute left-0">{fmt(low)}</span>
        {ticks.map(t => (
          <span
            key={t.label}
            className="absolute -translate-x-1/2 tabular-nums"
            style={{ left: `${t.pct}%` }}
          >
            {t.label}
          </span>
        ))}
        <span className="absolute right-0">{fmt(high)}</span>
      </div>
    </div>
  )
}

function MaRow({ level, price }: { level: MaLevel; price?: number }) {
  if (!level.available) {
    return (
      <div className="flex items-center justify-between gap-2 py-1.5 text-xs text-muted-foreground/50">
        <span>MA{level.window}</span>
        <span>not enough history</span>
      </div>
    )
  }

  const slopeLabel =
    level.slope_annualised_percent != null
      ? `${level.direction} ${signed(level.slope_annualised_percent, 1)}%/yr annualised (21 sessions)`
      : 'slope unavailable'

  // Arrow and colour both come from the distance itself, so they can never
  // disagree with the number they sit next to. The MA's own slope is in the
  // row tooltip — it describes the average, not price's position against it.
  const distance = level.distance_percent
  const arrow = distance == null || distance === 0 ? '' : distance > 0 ? '↑' : '↓'

  return (
    <div
      className="flex items-center gap-2 border-b border-border/40 py-1.5 last:border-b-0"
      title={
        price != null
          ? `Price ${fmt(price)} vs MA${level.window} ${fmt(level.sma)} — ${slopeLabel}`
          : slopeLabel
      }
    >
      <span className="w-10 shrink-0 text-[11px] font-medium text-foreground">MA{level.window}</span>
      <span className="min-w-0 flex-1 text-right text-[11px] tabular-nums text-muted-foreground">
        {fmt(level.sma)}
      </span>
      <span
        className={cn(
          'w-[62px] shrink-0 text-right text-[11px] font-medium tabular-nums',
          tone(distance)
        )}
      >
        {arrow}
        {signed(distance)}%
      </span>
    </div>
  )
}

/** The header moved up into <Section>; these are what it renders. */
export const TECHNICALS_SUBLABEL =
  'RSI(14) · MACD(12,26,9) · SMA 5/20/50/200/250 · 52w range — from adjusted daily bars'

interface TechnicalPanelProps {
  symbol: string
}

export default function TechnicalPanel({ symbol }: TechnicalPanelProps) {
  const res = useCachedResource(
    K.technicals(symbol),
    () => api.market.technicals(symbol),
    { ttl: TTL.technicals },
  )
  const data = res.data ?? null
  const loading = res.isLoading
  const error = res.error?.message ?? null

  // The explanation lives in the cache rather than in state, so a model round
  // trip survives leaving the page. Keyed by symbol, so there is nothing to
  // reset when the symbol changes — the key simply points somewhere else.
  const explainKey = K.technicalsExplain(symbol)
  const explanation = useCachedValue<TechnicalsExplanation>(explainKey).value ?? null
  const [explaining, setExplaining] = useState(false)

  async function requestExplanation() {
    setExplaining(true)
    try {
      await cached(explainKey, () => api.market.explainTechnicals(symbol), {
        ttl: TTL.explanation,
      })
    } catch (e) {
      // A failure renders as an unavailable explanation, as it always has.
      put<TechnicalsExplanation>(explainKey, {
        symbol,
        available: false,
        reason: e instanceof Error ? e.message : 'Request failed',
      })
    } finally {
      setExplaining(false)
    }
  }

  // Which extra benchmarks to compare against (gold, oil, …). A preference, so
  // it persists; keyed into the request so switching it refetches and caches.
  const [compare, setCompare] = useState<string[]>(readCompare)
  const [compareDraft, setCompareDraft] = useState('')
  useEffect(() => {
    writeJSON(RS_COMPARE_KEY, compare)
  }, [compare])

  function addCompare(sym: string) {
    const s = sym.toUpperCase()
    setCompare(prev => (prev.includes(s) || prev.length >= RS_MAX_COMPARE ? prev : [...prev, s]))
  }
  function toggleCompare(sym: string) {
    const s = sym.toUpperCase()
    setCompare(prev =>
      prev.includes(s)
        ? prev.filter(x => x !== s)
        : prev.length >= RS_MAX_COMPARE
          ? prev
          : [...prev, s]
    )
  }
  function submitDraft() {
    const s = compareDraft.trim().toUpperCase()
    setCompareDraft('')
    if (s && RS_TICKER.test(s)) addCompare(s)
  }

  // Relative strength + relative volume: its own request (SPY and the sector
  // ETF are extra bar reads), surfaced as two more cards in the same grid.
  const relative = useCachedResource(
    K.relative(symbol, compare),
    () => api.market.relative(symbol, compare),
    { ttl: TTL.relative },
  ).data

  const rsi = data?.rsi
  const macd = data?.macd
  const ladder = data?.moving_averages
  const drawdown = data?.drawdown
  const rvol = relative?.available ? relative.rvol : null
  const rsWindows = relative?.available ? (relative.windows ?? []) : []
  const rsBenchmarks = relative?.available ? (relative.benchmarks ?? []) : []
  // Custom chips the reader added beyond the presets, for removable display.
  const customCompare = compare.filter(s => !RS_PRESETS.some(p => p.sym === s))

  const rsiValue = rsi?.current_rsi
  const zoneColor =
    rsi?.zone === 'overbought'
      ? 'text-rose-400'
      : rsi?.zone === 'oversold'
        ? 'text-emerald-400'
        : 'text-foreground'

  return (
    // The panel is its own size container: the columns follow the width this
    // panel actually gets, not the viewport's. With the sidebar and watchlist
    // both open, a 1300px window leaves it ~680px — a viewport `xl:` breakpoint
    // fires there and crushes four cards into ~160px each.
    <div className="flex flex-col gap-3 [container-type:inline-size]">
      {loading && !data ? (
        <Card>
          <CardContent className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 size={16} className="animate-spin" />
            Computing indicators for {symbol}…
          </CardContent>
        </Card>
      ) : error ? (
        <Card>
          <CardContent className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <AlertCircle size={15} />
            {error}
          </CardContent>
        </Card>
      ) : (
        // Four across once each card gets ~225px (what the MA ladder's three
        // columns need), 2×2 below that, one column on a phone — never 3 + 1,
        // which left one card stranded on a row of its own.
        <div className="grid gap-3 [@container(min-width:480px)]:grid-cols-2 [@container(min-width:940px)]:grid-cols-4">
          {/* RSI */}
          <Card>
            <CardContent className="flex h-full flex-col gap-2.5 py-4">
              <div className="flex items-baseline justify-between">
                <span className="text-[13px] font-semibold text-foreground">RSI</span>
                <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                  {rsi?.period ?? 14}-day
                </span>
              </div>

              {rsi?.error || rsiValue == null ? (
                <p className="py-3 text-xs text-muted-foreground/60">{rsi?.error ?? 'No data'}</p>
              ) : (
                <>
                  <div className="flex items-baseline gap-2">
                    <span className={cn('text-2xl font-bold tabular-nums', zoneColor)}>
                      {fmt(rsiValue, 1)}
                    </span>
                    <Badge
                      variant="secondary"
                      className={cn('text-[10px] capitalize', zoneColor)}
                    >
                      {rsi?.zone}
                    </Badge>
                  </div>

                  <RsiGauge value={rsiValue} />
                  <div className="flex justify-between text-[9px] text-muted-foreground/50">
                    <span>0</span>
                    <span>30 oversold</span>
                    <span>70 overbought</span>
                    <span>100</span>
                  </div>

                  {/* The gauge says where RSI is; this says how it got there.
                      mt-auto pins it to the foot of the card, whose height the
                      taller MA ladder sets. */}
                  <div className="mt-auto pt-1">
                    <RsiSparkline
                      values={(rsi?.historical ?? []).filter(
                        (v): v is number => typeof v === 'number' && Number.isFinite(v)
                      )}
                      className={zoneColor === 'text-foreground' ? 'text-sky-400' : zoneColor}
                    />
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          {/* MACD */}
          <Card>
            <CardContent className="flex h-full flex-col gap-2.5 py-4">
              <div className="flex items-baseline justify-between">
                <span className="text-[13px] font-semibold text-foreground">MACD</span>
                <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                  12 / 26 / 9
                </span>
              </div>

              {macd?.error ? (
                <p className="py-3 text-xs text-muted-foreground/60">{macd.error}</p>
              ) : (
                <>
                  <div className="flex items-baseline gap-2">
                    <span className={cn('text-2xl font-bold tabular-nums', tone(macd?.histogram))}>
                      {signed(macd?.histogram, macdDigits(macd?.histogram))}
                    </span>
                    <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                      histogram
                    </span>
                  </div>

                  <div className="flex flex-col gap-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">MACD line</span>
                      <span className="tabular-nums text-foreground">{fmt(macd?.macd, macdDigits(macd?.macd))}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Signal line</span>
                      <span className="tabular-nums text-foreground">{fmt(macd?.signal, macdDigits(macd?.signal))}</span>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-1.5">
                    <Badge variant="secondary" className={cn('text-[10px] capitalize', tone(macd?.histogram))}>
                      {macd?.trend ?? '—'} momentum
                    </Badge>
                    {macd?.bullish_crossover && (
                      <Badge variant="secondary" className="text-[10px] text-emerald-400">
                        bullish crossover
                      </Badge>
                    )}
                    {macd?.bearish_crossover && (
                      <Badge variant="secondary" className="text-[10px] text-rose-400">
                        bearish crossover
                      </Badge>
                    )}
                  </div>

                  {/* Same footing as the RSI sparkline: pinned to the foot of
                      the card, filling the height the MA ladder sets. */}
                  <div className="mt-auto pt-1">
                    <MacdHistogramBars
                      values={(macd?.history ?? []).filter(
                        (v): v is number => typeof v === 'number' && Number.isFinite(v)
                      )}
                    />
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          {/* Moving-average ladder */}
          <Card>
            <CardContent className="flex flex-col gap-2 py-4">
              <div className="flex items-baseline justify-between">
                <span className="text-[13px] font-semibold text-foreground">Moving averages</span>
                <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                  price vs MA
                </span>
              </div>

              {ladder?.error ? (
                <p className="py-3 text-xs text-muted-foreground/60">{ladder.error}</p>
              ) : (
                <>
                  <div className="flex flex-col">
                    {(ladder?.levels ?? []).map(level => (
                      <MaRow key={level.window} level={level} price={ladder?.current_price} />
                    ))}
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5 pt-1">
                    <Badge
                      variant="secondary"
                      className={cn(
                        'text-[10px] capitalize',
                        ladder?.alignment === 'bullish'
                          ? 'text-emerald-400'
                          : ladder?.alignment === 'bearish'
                            ? 'text-rose-400'
                            : 'text-muted-foreground'
                      )}
                    >
                      {ladder?.alignment ?? '—'} stack
                    </Badge>
                    <span className="text-[10px] text-muted-foreground/60">
                      price above {ladder?.above_count ?? 0} of {ladder?.total_count ?? 0}
                    </span>
                    {(ladder?.crosses ?? []).map(c => (
                      <Badge
                        key={`${c.fast}-${c.slow}`}
                        variant="secondary"
                        className={cn(
                          'text-[10px]',
                          c.type === 'bullish' ? 'text-emerald-400' : 'text-rose-400'
                        )}
                      >
                        {c.fast}/{c.slow} {c.type} cross
                      </Badge>
                    ))}
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          {/* Drawdown — where price stands against its own 52-week range */}
          <Card>
            <CardContent className="flex flex-col gap-2.5 py-4">
              <div className="flex items-baseline justify-between">
                <span className="text-[13px] font-semibold text-foreground">Off the high</span>
                <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                  52 weeks
                </span>
              </div>

              {drawdown?.error || drawdown?.from_high_percent == null ? (
                <p className="py-3 text-xs text-muted-foreground/60">
                  {drawdown?.error ?? 'No data'}
                </p>
              ) : (
                <>
                  <div className="flex items-baseline gap-2">
                    <span
                      className={cn(
                        'text-2xl font-bold tabular-nums',
                        DRAWDOWN_TONE[drawdown.status ?? 'pullback'].text
                      )}
                    >
                      {signed(drawdown.from_high_percent, 1)}%
                    </span>
                    <Badge
                      variant="secondary"
                      className={cn(
                        'text-[10px]',
                        DRAWDOWN_TONE[drawdown.status ?? 'pullback'].text
                      )}
                    >
                      {DRAWDOWN_LABELS[drawdown.status ?? 'pullback']}
                    </Badge>
                  </div>

                  <RangeBar profile={drawdown} />

                  <div className="flex flex-col gap-1 pt-1 text-[11px]">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">
                        52w high{drawdown.high_52w_date ? ` · ${drawdown.high_52w_date}` : ''}
                      </span>
                      <span className="tabular-nums text-foreground">{fmt(drawdown.high_52w)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">
                        Above the low
                      </span>
                      <span className="tabular-nums text-emerald-400">
                        {signed(drawdown.from_low_percent, 1)}%
                      </span>
                    </div>
                    {drawdown.worst && (
                      <div
                        className="flex justify-between"
                        title={`Peak ${drawdown.worst.peak_date} → trough ${drawdown.worst.trough_date}`}
                      >
                        <span className="text-muted-foreground">
                          Worst fall (5y){drawdown.worst.recovered ? '' : ' · not recovered'}
                        </span>
                        <span className="tabular-nums text-rose-400">
                          {signed(drawdown.worst.depth_percent, 1)}%
                        </span>
                      </div>
                    )}
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          {/* Relative strength — excess return vs SPY and the sector ETF */}
          <Card>
            <CardContent className="flex h-full flex-col gap-2.5 py-4">
              <div className="flex items-baseline justify-between">
                <span className="text-[13px] font-semibold text-foreground">Relative strength</span>
                <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                  excess return
                </span>
              </div>

              {rsWindows.length === 0 ? (
                <p className="py-3 text-xs text-muted-foreground/60">
                  {relative && !relative.available
                    ? (relative.reason ?? 'No data')
                    : 'Not enough history.'}
                </p>
              ) : (
                <>
                  <span className="text-2xl font-bold capitalize text-foreground">
                    {relative?.trend_label ?? '—'}
                  </span>

                  {/* Rows = benchmarks, columns = windows, so adding a custom
                      benchmark grows the card down rather than needing width. */}
                  <div
                    className="mt-1 grid items-center gap-x-2 gap-y-1 text-[11px] tabular-nums"
                    style={{ gridTemplateColumns: `minmax(0,1fr) repeat(${rsWindows.length}, auto)` }}
                  >
                    <span className="text-[9px] uppercase tracking-wide text-muted-foreground/50">
                      vs
                    </span>
                    {rsWindows.map(w => (
                      <span
                        key={w}
                        className="text-right text-[9px] uppercase tracking-wide text-muted-foreground/50"
                      >
                        {w}
                      </span>
                    ))}
                    {rsBenchmarks.map(b => (
                      <Fragment key={b.symbol}>
                        <span className="truncate text-muted-foreground" title={b.symbol}>
                          {b.label}
                        </span>
                        {rsWindows.map(w => (
                          <span key={w} className={cn('text-right', tone(b.excess[w]))}>
                            {b.no_data ? '—' : pctSigned(b.excess[w])}
                          </span>
                        ))}
                      </Fragment>
                    ))}
                  </div>

                  {/* Compare against custom benchmarks — presets plus a free ticker. */}
                  <div className="mt-auto flex flex-wrap items-center gap-1 border-t border-border/40 pt-2">
                    <span className="mr-0.5 text-[9px] uppercase tracking-wide text-muted-foreground/50">
                      compare
                    </span>
                    {RS_PRESETS.map(p => (
                      <button
                        key={p.sym}
                        type="button"
                        onClick={() => toggleCompare(p.sym)}
                        className={cn(
                          'rounded px-1.5 py-0.5 text-[10px] transition-colors',
                          compare.includes(p.sym)
                            ? 'bg-sky-500/20 text-sky-300'
                            : 'bg-muted text-muted-foreground hover:text-foreground'
                        )}
                      >
                        {p.label}
                      </button>
                    ))}
                    {customCompare.map(sym => (
                      <button
                        key={sym}
                        type="button"
                        onClick={() => toggleCompare(sym)}
                        title="Remove"
                        className="rounded bg-sky-500/20 px-1.5 py-0.5 text-[10px] text-sky-300"
                      >
                        {sym} ×
                      </button>
                    ))}
                    {compare.length < RS_MAX_COMPARE && (
                      <input
                        value={compareDraft}
                        onChange={e => setCompareDraft(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter') {
                            e.preventDefault()
                            submitDraft()
                          }
                        }}
                        onBlur={submitDraft}
                        placeholder="+ ticker"
                        className="w-16 rounded bg-muted px-1.5 py-0.5 text-[10px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none"
                      />
                    )}
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          {/* Relative volume — today's volume against its own 20-day average */}
          <Card>
            <CardContent className="flex h-full flex-col gap-2.5 py-4">
              <div className="flex items-baseline justify-between">
                <span className="text-[13px] font-semibold text-foreground">Relative volume</span>
                <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                  vs {rvol?.window ?? 20}-day avg
                </span>
              </div>

              {!rvol ? (
                <p className="py-3 text-xs text-muted-foreground/60">No volume data.</p>
              ) : (
                <>
                  <div className="flex items-baseline gap-2">
                    <span className="text-2xl font-bold tabular-nums text-foreground">
                      {fmt(rvol.value, 1)}×
                    </span>
                    <Badge variant="secondary" className="text-[10px] capitalize">
                      {rvol.label}
                    </Badge>
                  </div>
                  <div className="mt-auto flex flex-col gap-1 pt-1 text-[11px]">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Today</span>
                      <span className="tabular-nums text-foreground">
                        {compactVol(rvol.today_volume)}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Avg ({rvol.window}d)</span>
                      <span className="tabular-nums text-foreground">
                        {compactVol(rvol.avg_volume)}
                      </span>
                    </div>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* LLM read */}
      <Card>
        <CardContent className="flex flex-col gap-3 py-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[13px] font-semibold text-foreground">What this reads like</span>
            <Button
              size="sm"
              variant={explanation ? 'outline' : 'default'}
              className="h-7 text-xs"
              onClick={requestExplanation}
              disabled={explaining || loading || !data}
            >
              {explaining ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Sparkles size={13} />
              )}
              {explaining ? 'Reading…' : explanation ? 'Regenerate' : 'Explain these indicators'}
            </Button>
          </div>

          {!explanation && !explaining && (
            <p className="text-xs text-muted-foreground/60">
              Sends the readings above to the model for a plain-English summary of what they
              show. Nothing is sent until you ask.
            </p>
          )}

          {explanation?.available === false && (
            <div className="flex items-start gap-2 text-xs text-muted-foreground">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>{explanation.reason}</span>
            </div>
          )}

          {explanation?.explanation && (
            <>
              <Separator />
              <div className="flex flex-col gap-2">
                {explanation.explanation.split('\n\n').map((para, i) => (
                  <p key={i} className="text-sm leading-relaxed text-foreground/90">
                    {para}
                  </p>
                ))}
              </div>
              <p className="text-[10px] text-muted-foreground/50">
                {explanation.model} · {explanation.disclaimer}
              </p>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
