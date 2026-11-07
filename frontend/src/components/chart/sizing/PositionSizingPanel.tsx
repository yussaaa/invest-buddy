/**
 * Position sizing and the ATR volatility stop.
 *
 * The one panel on this page whose inputs are personal — account equity, the
 * fraction of it you'll risk, where you'd get in — so the arithmetic runs in
 * the browser and nothing about your account leaves it. The only real-data
 * input is ATR, fetched from the backend; everything else is fixed-fractional
 * position sizing, which is deterministic. Equity and risk-per-trade persist to
 * local storage, because they are a property of the trader, not the ticker.
 *
 * This describes a method for sizing a hypothetical trade. It is not advice to
 * take one.
 */

import { useEffect, useState } from 'react'
import { AlertCircle, Loader2, RotateCcw } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { cn, readJSON, writeJSON } from '@/lib/utils'
import { api } from '@/lib/api'
import { K, TTL } from '@/lib/cacheKeys'
import { useCachedResource } from '@/hooks/useCachedResource'
import { money } from '../valuation/format'

export const SIZING_SUBLABEL =
  'Fixed-fractional risk · ATR volatility stop · R-multiple target'

const PREFS_KEY = 'agent-invest.charting.sizing'
type SizingPrefs = { equity: number; riskPct: number }
const DEFAULT_PREFS: SizingPrefs = { equity: 25_000, riskPct: 1 }

type Direction = 'long' | 'short'
const ATR_MULTS = [1, 1.5, 2, 2.5, 3]
const R_TARGETS = [1, 2, 3]

function readPrefs(): SizingPrefs {
  const p = readJSON<Partial<SizingPrefs>>(PREFS_KEY, {}, localStorage)
  return {
    equity: typeof p.equity === 'number' && p.equity > 0 ? p.equity : DEFAULT_PREFS.equity,
    riskPct:
      typeof p.riskPct === 'number' && p.riskPct > 0 ? p.riskPct : DEFAULT_PREFS.riskPct,
  }
}

function Stat({
  label,
  value,
  detail,
  tone,
}: {
  label: string
  value: string
  detail?: string
  tone?: string
}) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60">{label}</p>
      <p className="mt-0.5 text-lg font-bold tabular-nums text-foreground">{value}</p>
      {detail && <p className={cn('mt-0.5 text-[11px] tabular-nums', tone)}>{detail}</p>}
    </div>
  )
}

/** A labelled numeric field with an optional prefix/suffix adornment. */
function Field({
  label,
  value,
  onChange,
  prefix,
  suffix,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  prefix?: string
  suffix?: string
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">{label}</span>
      <div className="flex items-center gap-1.5">
        {prefix && <span className="text-xs text-muted-foreground">{prefix}</span>}
        <Input
          type="number"
          inputMode="decimal"
          value={value}
          onChange={e => onChange(e.target.value)}
          className="h-8 tabular-nums"
        />
        {suffix && <span className="text-xs text-muted-foreground">{suffix}</span>}
      </div>
    </label>
  )
}

export default function PositionSizingPanel({ symbol }: { symbol: string }) {
  const res = useCachedResource(K.atr(symbol), () => api.market.atr(symbol), { ttl: TTL.atr })

  const [prefs, setPrefs] = useState<SizingPrefs>(readPrefs)
  const [equityStr, setEquityStr] = useState(String(prefs.equity))
  const [riskStr, setRiskStr] = useState(String(prefs.riskPct))
  const [direction, setDirection] = useState<Direction>('long')
  const [atrMult, setAtrMult] = useState(2)
  const [rTarget, setRTarget] = useState(2)
  const [entryStr, setEntryStr] = useState('')
  // null until the first auto-fill; tracks which symbol the entry was snapped
  // for, so a symbol change re-snaps but a cleared field does not.
  const [entrySym, setEntrySym] = useState<string | null>(null)

  const price = res.data?.price ?? null
  const atr = res.data?.atr ?? null

  // Equity and risk are the trader's, not the ticker's — persist them.
  useEffect(() => {
    const equity = parseFloat(equityStr)
    const riskPct = parseFloat(riskStr)
    if (equity > 0 && riskPct > 0) {
      const next = { equity, riskPct }
      setPrefs(next)
      writeJSON(PREFS_KEY, next, localStorage)
    }
  }, [equityStr, riskStr])

  // Snap the entry to the live price on first load and whenever the symbol
  // changes. Keyed on entrySym rather than an empty field, so the reader can
  // clear the entry and retype without it refilling from under them.
  useEffect(() => {
    if (price != null && entrySym !== symbol) {
      setEntryStr(price.toFixed(2))
      setEntrySym(symbol)
    }
  }, [symbol, price, entrySym])

  if (res.isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
          <Loader2 size={16} className="animate-spin" />
          Measuring {symbol}'s range…
        </CardContent>
      </Card>
    )
  }

  const error = res.error?.message ?? res.data?.error
  if (error || atr == null || price == null) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <AlertCircle size={15} />
          {error ?? 'No volatility data for this symbol.'}
        </CardContent>
      </Card>
    )
  }

  const equity = parseFloat(equityStr) || 0
  const riskPct = parseFloat(riskStr) || 0
  const entry = parseFloat(entryStr) || 0

  // Fixed-fractional sizing: risk a set fraction of equity, and let the stop
  // distance decide how many shares that buys.
  const dollarRisk = equity * (riskPct / 100)
  const perShareRisk = atrMult * atr // the ATR stop distance, in dollars
  const stopPrice = direction === 'long' ? entry - perShareRisk : entry + perShareRisk
  const targetPrice =
    direction === 'long' ? entry + rTarget * perShareRisk : entry - rTarget * perShareRisk
  const shares = perShareRisk > 0 ? Math.floor(dollarRisk / perShareRisk) : 0
  const positionValue = shares * entry
  const pctOfEquity = equity > 0 ? (positionValue / equity) * 100 : 0
  const reward = shares * rTarget * perShareRisk
  const needsMargin = pctOfEquity > 100

  const ready = equity > 0 && riskPct > 0 && entry > 0
  const stopInvalid = stopPrice <= 0

  return (
    <div className="flex flex-col gap-3">
      {/* Volatility context — the one real-data input. */}
      <Card>
        <CardContent className="grid grid-cols-3 gap-4 py-4">
          <Stat label="Price" value={`$${money(price)}`} />
          <Stat label={`ATR (${res.data?.period}d)`} value={`$${money(atr)}`} />
          <Stat
            label="ATR % of price"
            value={res.data?.atr_pct != null ? `${(res.data.atr_pct * 100).toFixed(1)}%` : '—'}
          />
        </CardContent>
      </Card>

      {/* Inputs */}
      <Card>
        <CardContent className="flex flex-col gap-4 py-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Field label="Account equity" value={equityStr} onChange={setEquityStr} prefix="$" />
            <Field label="Risk per trade" value={riskStr} onChange={setRiskStr} suffix="%" />
            <div className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                Direction
              </span>
              <div className="flex gap-1">
                {(['long', 'short'] as Direction[]).map(d => (
                  <Button
                    key={d}
                    type="button"
                    size="sm"
                    variant={direction === d ? 'default' : 'outline'}
                    className="h-8 flex-1 capitalize"
                    onClick={() => setDirection(d)}
                  >
                    {d}
                  </Button>
                ))}
              </div>
            </div>
            <Field label="Entry" value={entryStr} onChange={setEntryStr} prefix="$" />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                Stop — ATR multiple
              </span>
              <div className="flex gap-1">
                {ATR_MULTS.map(m => (
                  <Button
                    key={m}
                    type="button"
                    size="sm"
                    variant={atrMult === m ? 'default' : 'outline'}
                    className="h-8 flex-1 tabular-nums"
                    onClick={() => setAtrMult(m)}
                  >
                    {m}×
                  </Button>
                ))}
              </div>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                Target — R multiple
              </span>
              <div className="flex gap-1">
                {R_TARGETS.map(r => (
                  <Button
                    key={r}
                    type="button"
                    size="sm"
                    variant={rTarget === r ? 'default' : 'outline'}
                    className="h-8 flex-1 tabular-nums"
                    onClick={() => setRTarget(r)}
                  >
                    {r}R
                  </Button>
                ))}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Results */}
      <Card>
        <CardContent className="flex flex-col gap-3 py-4">
          {!ready ? (
            <p className="py-6 text-center text-xs text-muted-foreground/60">
              Enter account equity, risk and an entry price to size the trade.
            </p>
          ) : stopInvalid ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <AlertCircle size={15} />
              A {atrMult}× ATR stop sits at or below zero — tighten the multiple.
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
                <Stat
                  label="Shares"
                  value={shares.toLocaleString()}
                  detail={shares === 0 ? 'risk < one stop' : undefined}
                  tone={shares === 0 ? 'text-amber-400' : undefined}
                />
                <Stat label="Risk budget" value={`$${money(dollarRisk)}`} detail={`${riskPct}% of equity`} />
                <Stat
                  label="Stop"
                  value={`$${money(stopPrice)}`}
                  detail={`−${money(perShareRisk)} · ${((perShareRisk / entry) * 100).toFixed(1)}%`}
                  tone="text-rose-400"
                />
                <Stat
                  label="Target"
                  value={`$${money(targetPrice)}`}
                  detail={`+$${money(reward)}`}
                  tone="text-emerald-400"
                />
                <Stat label="Reward : risk" value={`${rTarget.toFixed(1)} : 1`} />
                <Stat
                  label="Position value"
                  value={`$${money(positionValue, 0)}`}
                  detail={`${pctOfEquity.toFixed(0)}% of equity`}
                  tone={needsMargin ? 'text-amber-400' : undefined}
                />
              </div>

              {needsMargin && shares > 0 && (
                <Badge
                  variant="secondary"
                  className="w-fit gap-1.5 text-[10px] uppercase tracking-wide text-amber-400"
                >
                  <AlertCircle size={11} />
                  Exceeds cash — needs {(positionValue / equity).toFixed(1)}× margin
                </Badge>
              )}
            </>
          )}

          <div className="flex items-center justify-between pt-1">
            <span className="text-[11px] text-muted-foreground/50">
              Sizing is deterministic; ATR is from delayed data. Not investment advice.
            </span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 gap-1 text-[11px]"
              onClick={() => {
                setDirection('long')
                setAtrMult(2)
                setRTarget(2)
                if (price != null) setEntryStr(price.toFixed(2))
              }}
            >
              <RotateCcw size={12} />
              Reset
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
