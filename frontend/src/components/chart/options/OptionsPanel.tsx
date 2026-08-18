/**
 * OptionsPanel — a screener over the option chain for the charted symbol.
 *
 * Everything numeric here is computed deterministically in Python: greeks from
 * Black-Scholes, probabilities under a risk-neutral lognormal assumption,
 * yields from the credit and the collateral. The model is only ever asked to
 * describe the result, and only when the button is pressed.
 *
 * This is a screener, not a recommendation engine, and the wording is
 * load-bearing rather than decorative. It ranks contracts that pass a filter
 * and shows how they were ranked; it does not size positions, does not read
 * the user's holdings, and does not mark anything as a pick.
 */

import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, AlertTriangle, Loader2, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { cn, readJSON, writeJSON } from '@/lib/utils'
import { api } from '@/lib/api'
import { cached, put } from '@/lib/clientCache'
import { K, TTL } from '@/lib/cacheKeys'
import { useCachedResource, useCachedValue } from '@/hooks/useCachedResource'
import type {
  OptionsExplanation,
  OptionStrategyKey,
} from '@/lib/types'
import StrategyTable from './StrategyTable'
import VolatilityContextCards from './VolatilityContext'

const STRATEGY_STORAGE_KEY = 'agent-invest.options.strategy'

const STRATEGIES: { key: OptionStrategyKey; label: string; blurb: string }[] = [
  {
    key: 'csp',
    label: 'Cash-secured put',
    blurb:
      'Sell a put below the market and hold the full strike in cash. Keeps the credit if the stock stays above the breakeven; obligates the seller to buy 100 shares at the strike if it does not.',
  },
  {
    key: 'covered_call',
    label: 'Covered call',
    blurb:
      'Sell a call above the market against 100 shares already held. Adds the premium to the position and caps its upside at the strike.',
  },
  {
    key: 'leaps_call',
    label: 'LEAPS call',
    blurb:
      'Buy a long-dated call deep in the money as a stock substitute. Costs a debit rather than earning a credit, and the time premium decays to zero by expiry.',
  },
  {
    key: 'put_credit_spread',
    label: 'Put credit spread',
    blurb:
      'Sell a put and buy a lower one against it. Collects a smaller credit than the put alone, and posts the width rather than the whole strike, so the worst case is known in advance.',
  },
]

function isStrategy(value: unknown): value is OptionStrategyKey {
  return STRATEGIES.some(s => s.key === value)
}

interface OptionsPanelProps {
  symbol: string
}

export default function OptionsPanel({ symbol }: OptionsPanelProps) {
  // One request for every strategy, filtered client-side by the pills. Switching
  // strategy is then instant and costs neither a request nor a cache key.
  const res = useCachedResource(
    K.optionStrategies(symbol),
    () => api.market.optionStrategies(symbol, 'all', 15),
    { ttl: TTL.optionStrategies },
  )
  const data = res.data ?? null
  const loading = res.isLoading
  const error = res.error?.message ?? null

  const [strategy, setStrategy] = useState<OptionStrategyKey>(() => {
    const saved = readJSON<unknown>(STRATEGY_STORAGE_KEY, 'csp')
    return isStrategy(saved) ? saved : 'csp'
  })

  useEffect(() => {
    writeJSON(STRATEGY_STORAGE_KEY, strategy)
  }, [strategy])

  // Keyed by symbol *and* strategy, so reading one screen's explanation does
  // not clobber another's — and so both survive leaving the page.
  const explainKey = K.optionsExplain(symbol, strategy)
  const explanation = useCachedValue<OptionsExplanation>(explainKey).value ?? null
  const [explaining, setExplaining] = useState(false)

  async function requestExplanation() {
    setExplaining(true)
    try {
      await cached(explainKey, () => api.market.explainOptions(symbol, strategy), {
        ttl: TTL.explanation,
      })
    } catch (e) {
      put<OptionsExplanation>(explainKey, {
        symbol,
        available: false,
        reason: e instanceof Error ? e.message : 'Request failed',
      })
    } finally {
      setExplaining(false)
    }
  }

  const visible = useMemo(
    () => (data?.candidates ?? []).filter(c => c.strategy === strategy),
    [data, strategy]
  )

  const counts = data?.counts_by_strategy ?? {}
  const universe = data?.universe_counts
  const active = STRATEGIES.find(s => s.key === strategy)
  const shortestDte = useMemo(
    () =>
      (data?.candidates ?? []).reduce<number | undefined>(
        (min, c) => (min == null || c.dte < min ? c.dte : min),
        undefined
      ),
    [data]
  )

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Options screener
        </h2>
        <span className="text-[11px] text-muted-foreground/60">
          Chain via yfinance (delayed) · greeks computed Black-Scholes · probabilities
          risk-neutral
        </span>
      </div>

      {loading && !data ? (
        <Card>
          <CardContent className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 size={16} className="animate-spin" />
            Reading the option chain for {symbol}…
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
        <>
          <VolatilityContextCards
            volatility={data?.volatility}
            expiries={data?.expiries}
            nextEarnings={data?.next_earnings}
            shortestDte={shortestDte}
          />

          {(data?.warnings ?? []).length > 0 && (
            <div className="flex flex-col gap-1">
              {data?.warnings?.map(w => (
                <p
                  key={w}
                  className="flex items-start gap-1.5 text-[11px] text-muted-foreground/70"
                >
                  <AlertCircle size={12} className="mt-0.5 shrink-0" />
                  {w}
                </p>
              ))}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-1 rounded-lg border border-border bg-card p-1">
            {STRATEGIES.map(s => (
              <button
                key={s.key}
                onClick={() => setStrategy(s.key)}
                className={cn(
                  'h-7 rounded px-2.5 text-xs font-medium transition-colors',
                  strategy === s.key
                    ? 'bg-primary/20 text-primary'
                    : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                )}
              >
                {s.label}
                <span className="ml-1.5 text-[10px] opacity-60">{counts[s.key] ?? 0}</span>
              </button>
            ))}
          </div>

          {active && (
            <p className="text-[11px] leading-relaxed text-muted-foreground/70">{active.blurb}</p>
          )}

          <Card>
            <CardContent className="p-2">
              {visible.length === 0 ? (
                <div className="flex flex-col items-center gap-1.5 py-8 text-center">
                  <p className="text-sm text-muted-foreground">
                    No contracts passed this screen for {symbol}.
                  </p>
                  {universe && (
                    <p className="text-xs text-muted-foreground/60">
                      {universe.scanned} contracts scanned · {universe.passed_liquidity} passed
                      liquidity · {universe.passed_moneyness} in the strike and expiry bands ·{' '}
                      {universe.ranked} ranked overall
                    </p>
                  )}
                  <p className="max-w-md text-[11px] text-muted-foreground/50">
                    Illiquid names, empty order books outside market hours, and chains with no
                    long-dated expiries all produce this.
                  </p>
                </div>
              ) : (
                <StrategyTable strategy={strategy} candidates={visible} />
              )}
            </CardContent>
          </Card>

          {/* Permanent, not collapsible: the ranking itself is the thing that
              needs the caveat, so it cannot be dismissed away from the table. */}
          <Card className="border-amber-500/20 bg-amber-500/5">
            <CardContent className="flex items-start gap-2 py-3">
              <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-400/80" />
              <p className="text-xs leading-relaxed text-muted-foreground">
                A high probability of profit is not a high expected return. This screen ranks
                contracts that win often and lose big — a 90%-probability short put collects a
                small credit while carrying the full downside of 100 shares. Probabilities are
                model-implied under a risk-neutral lognormal assumption, not forecasts. Early
                assignment, gap risk, and earnings inside the holding period are not modelled.
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="flex flex-col gap-3 py-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-[13px] font-semibold text-foreground">
                  What this screen shows
                </span>
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
                  {explaining
                    ? 'Reading…'
                    : explanation
                      ? 'Regenerate'
                      : 'Explain these candidates'}
                </Button>
              </div>

              {!explanation && !explaining && (
                <p className="text-xs text-muted-foreground/60">
                  Sends the volatility summary and the ranked rows above to the model for a
                  plain-English description of what the screen surfaced. Nothing is sent until
                  you ask.
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
        </>
      )}
    </div>
  )
}
