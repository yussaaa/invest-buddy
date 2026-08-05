/**
 * ChartingPage — TradingView-style price chart with live quote header.
 *
 * Symbol comes from ?ticker= so the watchlist panel and the analyze page stay
 * in sync with whatever is on screen.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertCircle,
  AreaChart,
  BarChart3,
  CandlestickChart,
  Loader2,
  LineChart,
  Search,
  Sparkles,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import type { Candle, History, InstrumentProfile } from '@/lib/types'
import { useQuotes } from '@/hooks/useQuotes'
import PriceChart, { type ChartType } from '@/components/chart/PriceChart'
import TechnicalPanel from '@/components/chart/TechnicalPanel'

const RANGES = ['1D', '5D', '1M', '3M', '6M', 'YTD', '1Y', '5Y', 'MAX'] as const

const CHART_TYPES: { value: ChartType; label: string; icon: typeof CandlestickChart }[] = [
  { value: 'candles', label: 'Candles', icon: CandlestickChart },
  { value: 'line', label: 'Line', icon: LineChart },
  { value: 'area', label: 'Area', icon: AreaChart },
]

const MA_OPTIONS = [20, 50, 200]

const DEFAULT_SYMBOL = 'AAPL'

function fmt(v?: number | null, digits = 2): string {
  if (v == null) return '—'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function fmtCompact(v?: number | null): string {
  if (v == null) return '—'
  if (Math.abs(v) >= 1e12) return `${(v / 1e12).toFixed(2)}T`
  if (Math.abs(v) >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(2)}M`
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(2)}K`
  return String(v)
}

function tone(v?: number | null) {
  if (v == null || v === 0) return 'text-muted-foreground'
  return v > 0 ? 'text-emerald-400' : 'text-rose-400'
}

function signed(v?: number | null, suffix = ''): string {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${fmt(v)}${suffix}`
}

function Stat({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">{label}</span>
      <span className={cn('text-sm tabular-nums text-foreground', className)}>{value}</span>
    </div>
  )
}

export default function ChartingPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const symbol = (searchParams.get('ticker') || DEFAULT_SYMBOL).toUpperCase()

  const [draft, setDraft] = useState(symbol)
  const [range, setRange] = useState<string>('1Y')
  const [chartType, setChartType] = useState<ChartType>('candles')
  const [maPeriods, setMaPeriods] = useState<number[]>([50])

  const [history, setHistory] = useState<History | null>(null)
  const [profile, setProfile] = useState<InstrumentProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [hovered, setHovered] = useState<Candle | null>(null)

  const symbols = useMemo(() => [symbol], [symbol])
  const { quotes } = useQuotes(symbols, 15000)
  const quote = quotes[symbol]

  useEffect(() => setDraft(symbol), [symbol])

  // Candles + profile for the selected symbol/range
  const abortRef = useRef<AbortController | null>(null)
  useEffect(() => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(null)

    Promise.all([
      api.market.history(symbol, range, controller.signal),
      api.market.profile(symbol, controller.signal),
    ])
      .then(([hist, prof]) => {
        if (controller.signal.aborted) return
        setHistory(hist)
        setProfile(prof)
        if (hist.error || hist.candles.length === 0) {
          setError(`No price data for ${symbol}`)
        }
      })
      .catch(e => {
        if (controller.signal.aborted) return
        setError(e instanceof Error ? e.message : 'Failed to load chart')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => controller.abort()
  }, [symbol, range])

  function submitSymbol(e: React.FormEvent) {
    e.preventDefault()
    const next = draft.trim().toUpperCase()
    if (next) setSearchParams({ ticker: next })
  }

  function toggleMa(period: number) {
    setMaPeriods(prev =>
      prev.includes(period) ? prev.filter(p => p !== period) : [...prev, period].sort((a, b) => a - b)
    )
  }

  const candles = history?.candles ?? []
  const latest: Candle | undefined = candles[candles.length - 1]
  const last = quote?.last ?? latest?.close
  const change = quote?.change
  const changePct = quote?.change_percent
  const periodPct = history?.period_change_percent

  const bar = hovered ?? latest

  return (
    <div className="max-w-[1500px] mx-auto px-6 py-6 flex flex-col gap-4">
      {/* Symbol header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl font-bold text-foreground">{symbol.replace(/^\^/, '')}</h1>
            {profile?.exchange && (
              <Badge variant="secondary" className="text-[10px]">{profile.exchange}</Badge>
            )}
            {profile?.sector && (
              <span className="text-xs text-muted-foreground">{profile.sector}</span>
            )}
          </div>
          <p className="text-sm text-muted-foreground">{profile?.name ?? '—'}</p>
        </div>

        <div className="flex items-end gap-4">
          <div className="flex items-baseline gap-2">
            <span className={cn('text-3xl font-bold tabular-nums', tone(change))}>{fmt(last)}</span>
            <span className={cn('text-sm font-medium tabular-nums', tone(change))}>
              {signed(change)} ({signed(changePct, '%')})
            </span>
          </div>

          <form onSubmit={submitSymbol} className="flex items-center gap-2">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={draft}
                onChange={e => setDraft(e.target.value.toUpperCase())}
                placeholder="Symbol"
                maxLength={12}
                className="h-9 w-32 pl-7 font-mono text-sm"
              />
            </div>
            <Button type="submit" variant="outline" size="sm" className="h-9">Go</Button>
            <Button
              type="button"
              size="sm"
              className="h-9"
              onClick={() => navigate(`/analyze?ticker=${encodeURIComponent(symbol)}`)}
            >
              <Sparkles size={14} />
              Analyze
            </Button>
          </form>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1 rounded-lg border border-border bg-card p-1">
          {RANGES.map(r => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={cn(
                'px-2.5 h-7 rounded text-xs font-medium transition-colors',
                range === r
                  ? 'bg-primary/20 text-primary'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              )}
            >
              {r}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-3">
          {/* Moving averages */}
          <div className="flex items-center gap-1 rounded-lg border border-border bg-card p-1">
            <BarChart3 size={13} className="ml-1 text-muted-foreground" />
            {MA_OPTIONS.map(p => (
              <button
                key={p}
                onClick={() => toggleMa(p)}
                className={cn(
                  'px-2 h-7 rounded text-xs font-medium tabular-nums transition-colors',
                  maPeriods.includes(p)
                    ? 'bg-primary/20 text-primary'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                )}
              >
                MA{p}
              </button>
            ))}
          </div>

          {/* Chart type */}
          <div className="flex items-center gap-1 rounded-lg border border-border bg-card p-1">
            {CHART_TYPES.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                onClick={() => setChartType(value)}
                title={label}
                className={cn(
                  'grid place-items-center h-7 w-8 rounded transition-colors',
                  chartType === value
                    ? 'bg-primary/20 text-primary'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                )}
              >
                <Icon size={14} />
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle size={16} />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Chart */}
      <Card className="relative overflow-hidden">
        <CardContent className="p-2">
          {/* OHLC legend */}
          <div className="absolute left-4 top-3 z-10 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] tabular-nums pointer-events-none">
            <span className="font-semibold text-foreground">
              {symbol.replace(/^\^/, '')} · {range} · {history?.interval ?? ''}
            </span>
            {bar && (
              <>
                <span className="text-muted-foreground">O <span className="text-foreground">{fmt(bar.open)}</span></span>
                <span className="text-muted-foreground">H <span className="text-foreground">{fmt(bar.high)}</span></span>
                <span className="text-muted-foreground">L <span className="text-foreground">{fmt(bar.low)}</span></span>
                <span className="text-muted-foreground">C <span className="text-foreground">{fmt(bar.close)}</span></span>
                <span className="text-muted-foreground">Vol <span className="text-foreground">{fmtCompact(bar.volume)}</span></span>
              </>
            )}
            <span className={cn('font-medium', tone(periodPct))}>
              {range} change {signed(periodPct, '%')}
            </span>
          </div>

          {loading && candles.length === 0 ? (
            <div className="flex h-[520px] items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 size={16} className="animate-spin" />
              Loading {symbol}…
            </div>
          ) : (
            <PriceChart
              candles={candles}
              chartType={chartType}
              maPeriods={maPeriods}
              height={520}
              onHover={setHovered}
            />
          )}
        </CardContent>
      </Card>

      {/* Key stats */}
      <Card>
        <CardContent className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-4 py-4">
          <Stat label="Open" value={fmt(latest?.open)} />
          <Stat
            label="Day range"
            value={
              profile?.day_low != null
                ? `${fmt(profile.day_low)} – ${fmt(profile.day_high)}`
                : '—'
            }
          />
          <Stat
            label="52w range"
            value={
              profile?.week52_low != null
                ? `${fmt(profile.week52_low)} – ${fmt(profile.week52_high)}`
                : '—'
            }
          />
          <Stat label="Volume" value={fmtCompact(quote?.volume ?? latest?.volume)} />
          <Stat label="Avg volume" value={fmtCompact(profile?.avg_volume)} />
          <Stat label="Market cap" value={fmtCompact(profile?.market_cap)} />
          <Stat label="P/E (TTM)" value={fmt(profile?.pe_ratio)} />
          <Stat label="Forward P/E" value={fmt(profile?.forward_pe)} />
          <Stat label="Beta" value={fmt(profile?.beta)} />
          <Stat
            label="Dividend yield"
            value={profile?.dividend_yield != null ? `${fmt(profile.dividend_yield)}%` : '—'}
          />
          <Stat
            label={`${range} change`}
            value={signed(periodPct, '%')}
            className={tone(periodPct)}
          />
          <Stat label="Bars" value={String(candles.length)} />
        </CardContent>
      </Card>

      {/* Technical analysis */}
      <TechnicalPanel symbol={symbol} />

      <p className="text-[11px] text-muted-foreground/50">
        Prices via yfinance — delayed, not a live exchange feed. Not investment advice.
      </p>
    </div>
  )
}
