/**
 * TechnicalPanel — RSI, MACD and the 5/20/50/250 moving-average ladder for the
 * charted symbol, with an on-demand plain-English read from the fast model.
 *
 * The explanation is behind a button rather than automatic: it costs a model
 * call per symbol, and most of the time the numbers speak for themselves.
 */

import { useEffect, useRef, useState } from 'react'
import { AlertCircle, Loader2, Sparkles } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import type { MaLevel, Technicals, TechnicalsExplanation } from '@/lib/types'

function fmt(v?: number | null, digits = 2): string {
  if (v == null) return '—'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function signed(v?: number | null, digits = 2): string {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${fmt(v, digits)}`
}

function tone(v?: number | null) {
  if (v == null || v === 0) return 'text-muted-foreground'
  return v > 0 ? 'text-emerald-400' : 'text-rose-400'
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
    level.slope_percent_5d != null
      ? `${level.direction} ${signed(level.slope_percent_5d)}% over 5 sessions`
      : 'slope unavailable'

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
          level.above ? 'text-emerald-400' : 'text-rose-400'
        )}
      >
        <span className="text-muted-foreground/60">
          {level.direction === 'rising' ? '↑' : level.direction === 'falling' ? '↓' : ''}
        </span>
        {signed(level.distance_percent)}%
      </span>
    </div>
  )
}

interface TechnicalPanelProps {
  symbol: string
}

export default function TechnicalPanel({ symbol }: TechnicalPanelProps) {
  const [data, setData] = useState<Technicals | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [explanation, setExplanation] = useState<TechnicalsExplanation | null>(null)
  const [explaining, setExplaining] = useState(false)

  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(null)
    setExplanation(null)  // a new symbol invalidates the previous read

    api.market
      .technicals(symbol, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return
        setData(result)
      })
      .catch(e => {
        if (!controller.signal.aborted) {
          setError(e instanceof Error ? e.message : 'Failed to load indicators')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => controller.abort()
  }, [symbol])

  async function requestExplanation() {
    setExplaining(true)
    try {
      setExplanation(await api.market.explainTechnicals(symbol))
    } catch (e) {
      setExplanation({
        symbol,
        available: false,
        reason: e instanceof Error ? e.message : 'Request failed',
      })
    } finally {
      setExplaining(false)
    }
  }

  const rsi = data?.rsi
  const macd = data?.macd
  const ladder = data?.moving_averages

  const rsiValue = rsi?.current_rsi
  const zoneColor =
    rsi?.zone === 'overbought'
      ? 'text-rose-400'
      : rsi?.zone === 'oversold'
        ? 'text-emerald-400'
        : 'text-foreground'

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Technical analysis
        </h2>
        <span className="text-[11px] text-muted-foreground/60">
          RSI(14) · MACD(12,26,9) · SMA 5/20/50/250 — computed from daily closes
        </span>
      </div>

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
        <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
          {/* RSI */}
          <Card>
            <CardContent className="flex flex-col gap-2.5 py-4">
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
                </>
              )}
            </CardContent>
          </Card>

          {/* MACD */}
          <Card>
            <CardContent className="flex flex-col gap-2.5 py-4">
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
                      {signed(macd?.histogram, 3)}
                    </span>
                    <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                      histogram
                    </span>
                  </div>

                  <div className="flex flex-col gap-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">MACD line</span>
                      <span className="tabular-nums text-foreground">{fmt(macd?.macd, 3)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Signal line</span>
                      <span className="tabular-nums text-foreground">{fmt(macd?.signal, 3)}</span>
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
