/**
 * What the market is assuming about this company, and what a DCF makes of it.
 *
 * The headline is the reverse DCF — the growth rate today's price already
 * requires — rather than a fair value. That is the more robust number and the
 * more honest framing: a fair value is a price target with a model attached,
 * while an implied growth rate is a description of the price that anyone can
 * argue with. The forward model is here too, as a band rather than a point.
 */

import { useState } from 'react'
import { AlertCircle, Loader2, RotateCcw, ShieldCheck, Sparkles } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import { K, TTL } from '@/lib/cacheKeys'
import { cached, put } from '@/lib/clientCache'
import { useCachedResource, useCachedValue } from '@/hooks/useCachedResource'
import type { AssumptionSource, ScenarioCase, ValuationExplanation } from '@/lib/types'
import SensitivityGrid from './SensitivityGrid'
import { compact, money, percent, signedPercent } from './format'
import { ASYMMETRY_NOTE_RATIO, bandAsymmetry } from './band'

export const VALUATION_SUBLABEL =
  'Levered FCF at the cost of equity · 10-year linear growth fade · Gordon terminal value'

const CASE_LABEL: Record<ScenarioCase, string> = { bear: 'Bear', base: 'Base', bull: 'Bull' }


interface Overrides {
  growth?: number
  terminal_growth?: number
  discount_rate?: number
}

function SourceBadge({ source }: { source?: AssumptionSource }) {
  if (!source) return null
  return (
    <Badge
      variant="secondary"
      className={cn(
        'px-1.5 py-0 text-[9px] uppercase tracking-wide',
        source === 'user' && 'bg-primary/20 text-primary'
      )}
    >
      {source === 'derived' ? 'from filings' : source === 'user' ? 'yours' : 'default'}
    </Badge>
  )
}

/** A percentage assumption the reader can move. */
function Knob({
  label,
  value,
  source,
  min,
  max,
  step,
  onChange,
}: {
  label: string
  value: number
  source?: AssumptionSource
  min: number
  max: number
  step: number
  onChange: (v: number) => void
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] text-muted-foreground">{label}</span>
        <span className="flex items-center gap-1.5">
          <span className="text-[13px] font-semibold tabular-nums text-foreground">
            {percent(value)}
          </span>
          <SourceBadge source={source} />
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="h-1 w-full cursor-pointer appearance-none rounded-full bg-muted accent-primary"
      />
    </div>
  )
}

/**
 * The model's description of the valuation above, for exactly these overrides.
 *
 * The server checks the reply for verdict vocabulary and for numbers the
 * valuation does not contain, and swaps in a templated explanation when it
 * fails. `guarded` says when that happened — the reader is told rather than
 * shown a different voice without explanation.
 */
function ExplainCard({
  symbol,
  params,
  ready,
}: {
  symbol: string
  params: Record<string, number>
  ready: boolean
}) {
  const explainKey = K.valuationExplain(symbol, params)
  const explanation = useCachedValue<ValuationExplanation>(explainKey).value ?? null
  const [explaining, setExplaining] = useState(false)

  async function requestExplanation() {
    setExplaining(true)
    try {
      await cached(explainKey, () => api.market.explainValuation(symbol, params), {
        ttl: TTL.explanation,
      })
    } catch (e) {
      put<ValuationExplanation>(explainKey, {
        symbol,
        available: false,
        reason: e instanceof Error ? e.message : 'Request failed',
      })
    } finally {
      setExplaining(false)
    }
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[13px] font-semibold text-foreground">What this valuation says</span>
          <Button
            size="sm"
            variant={explanation ? 'outline' : 'default'}
            className="h-7 text-xs"
            onClick={requestExplanation}
            disabled={explaining || !ready}
          >
            {explaining ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
            {explaining ? 'Reading…' : explanation ? 'Regenerate' : 'Explain this valuation'}
          </Button>
        </div>

        {!explanation && !explaining && (
          <p className="text-xs text-muted-foreground/60">
            Sends the valuation above, with your assumptions, to the model for a plain-English
            description of what the price implies and how fragile the estimate is. Nothing is sent
            until you ask.
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
            {explanation.guarded && (
              <p className="flex items-center gap-1.5 text-[10px] text-amber-400/90">
                <ShieldCheck size={12} />
                The model's reply read as a verdict or quoted a figure not in the valuation, so
                this summary was written from the numbers directly.
              </p>
            )}
            <p className="text-[10px] text-muted-foreground/50">
              {explanation.model} · {explanation.disclaimer}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}

export default function ValuationPanel({ symbol }: { symbol: string }) {
  const [overrides, setOverrides] = useState<Overrides>({})

  const params: Record<string, number> = {}
  if (overrides.growth != null) params.growth = overrides.growth
  if (overrides.terminal_growth != null) params.terminal_growth = overrides.terminal_growth
  if (overrides.discount_rate != null) params.discount_rate = overrides.discount_rate

  const res = useCachedResource(
    K.valuation(symbol, params),
    () => api.market.valuation(symbol, params),
    { ttl: TTL.valuation, keepPreviousData: true },
  )

  if (res.isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
          <Loader2 size={16} className="animate-spin" />
          Reading {symbol}'s cash flow statement…
        </CardContent>
      </Card>
    )
  }

  const data = res.data
  const fatal = res.error?.message ?? (data?.error && !data.inputs ? data.error : null)
  if (fatal) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <AlertCircle size={15} />
          {fatal}
        </CardContent>
      </Card>
    )
  }

  const { assumptions, inputs, base_case: base, scenarios, sensitivity, analysts } = data ?? {}
  const price = inputs?.price
  const implied = data?.implied_growth
  const derived = inputs?.revenue_growth

  // Negative cash flow is refused upstream, but the inputs still render — the
  // reader should see *why* there is no valuation rather than an empty panel.
  const refused = data?.error && data.inputs
  const asymmetry = bandAsymmetry(scenarios)

  return (
    <div className="flex flex-col gap-3">
      {/* The headline */}
      <Card>
        <CardContent className="flex flex-col gap-3 py-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                Growth today's price implies
              </p>
              <p
                className={cn(
                  'mt-0.5 text-3xl font-bold tabular-nums',
                  implied == null ? 'text-muted-foreground' : 'text-foreground'
                )}
              >
                {implied == null ? '—' : `${(implied * 100).toFixed(1)}%`}
                {implied != null && (
                  <span className="ml-1 text-sm font-normal text-muted-foreground">
                    a year for {assumptions?.years ?? 10}
                  </span>
                )}
              </p>
              <p className="mt-1 max-w-xl text-[11px] leading-relaxed text-muted-foreground">
                {implied == null
                  ? 'No growth rate in a plausible range reproduces the current price from this cash flow.'
                  : derived != null
                    ? `Free cash flow would need to compound at this rate to justify ${money(price)}. Its most recent reported revenue growth was ${percent(derived)}.`
                    : `Free cash flow would need to compound at this rate to justify ${money(price)}.`}
              </p>
            </div>

            {analysts?.target_mean != null && (
              <div className="shrink-0 text-right">
                <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                  Analyst target
                </p>
                <p className="mt-0.5 text-lg font-bold tabular-nums text-foreground">
                  {money(analysts.target_mean)}
                </p>
                <p className="text-[10px] tabular-nums text-muted-foreground/60">
                  {money(analysts.target_low)} – {money(analysts.target_high)}
                  {analysts.count ? ` · ${analysts.count} analysts` : ''}
                </p>
              </div>
            )}
          </div>

          {refused && (
            <>
              <Separator />
              <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <AlertCircle size={14} />
                {data?.error}
              </p>
            </>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-3 xl:grid-cols-2">
        {/* Assumptions */}
        <Card>
          <CardContent className="flex flex-col gap-3 py-4">
            <div className="flex items-baseline justify-between">
              <span className="text-[13px] font-semibold text-foreground">Assumptions</span>
              {Object.keys(params).length > 0 && (
                <button
                  onClick={() => setOverrides({})}
                  className="flex items-center gap-1 text-[11px] text-primary transition-colors hover:text-primary/80"
                >
                  <RotateCcw size={11} />
                  Reset
                </button>
              )}
            </div>
            <Separator />

            {assumptions && (
              <div className="flex flex-col gap-3">
                <Knob
                  label="Year-1 FCF growth"
                  value={overrides.growth ?? assumptions.initial_growth}
                  source={assumptions.initial_growth_source}
                  min={-0.1}
                  max={0.4}
                  step={0.005}
                  onChange={v => setOverrides(o => ({ ...o, growth: v }))}
                />
                <Knob
                  label="Terminal growth"
                  value={overrides.terminal_growth ?? assumptions.terminal_growth}
                  source={assumptions.terminal_growth_source}
                  min={0}
                  max={0.045}
                  step={0.0025}
                  onChange={v => setOverrides(o => ({ ...o, terminal_growth: v }))}
                />
                <Knob
                  label="Discount rate (cost of equity)"
                  value={overrides.discount_rate ?? assumptions.discount_rate}
                  source={assumptions.discount_rate_source}
                  min={0.04}
                  max={0.2}
                  step={0.0025}
                  onChange={v => setOverrides(o => ({ ...o, discount_rate: v }))}
                />

                <p className="text-[10px] leading-relaxed text-muted-foreground/60">
                  Discount rate from CAPM on a beta of {assumptions.cost_of_equity.beta.toFixed(2)}
                  {assumptions.cost_of_equity.beta_clamped &&
                    ` (clamped from ${assumptions.cost_of_equity.raw_beta.toFixed(2)})`}
                  , growth fading linearly to terminal over {assumptions.years} years. Free cash
                  flow is levered, so it is discounted at the cost of equity and net debt is not
                  subtracted again.
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Scenario band */}
        <Card>
          <CardContent className="flex flex-col gap-3 py-4">
            <div className="flex items-baseline justify-between">
              <span className="text-[13px] font-semibold text-foreground">Scenario band</span>
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                per share
              </span>
            </div>
            <Separator />

            <div className="flex flex-col">
              {(scenarios ?? []).map(scenario => (
                <div
                  key={scenario.case}
                  className="flex items-center justify-between gap-3 border-b border-border/40 py-2 last:border-b-0"
                >
                  <span className="w-12 shrink-0 text-xs text-foreground">
                    {CASE_LABEL[scenario.case]}
                  </span>
                  <span className="flex-1 truncate text-[10px] tabular-nums text-muted-foreground/60">
                    g {percent(scenario.assumptions.initial_growth)} · term{' '}
                    {percent(scenario.assumptions.terminal_growth)} · r{' '}
                    {percent(scenario.assumptions.discount_rate)}
                  </span>
                  <span
                    className={cn(
                      'w-20 shrink-0 text-right text-sm font-semibold tabular-nums',
                      scenario.error
                        ? 'text-muted-foreground/40'
                        : price && (scenario.value_per_share ?? 0) >= price
                          ? 'text-emerald-400'
                          : 'text-rose-400'
                    )}
                    title={scenario.error}
                  >
                    {scenario.error ? '—' : money(scenario.value_per_share)}
                  </span>
                </div>
              ))}
            </div>

            {asymmetry && asymmetry.ratio > ASYMMETRY_NOTE_RATIO && (
              <p className="text-[10px] leading-relaxed text-muted-foreground/60">
                Bull is {signedPercent(asymmetry.upside)} from base, bear{' '}
                {signedPercent(-asymmetry.downside)}. The steps are symmetric, but a DCF is
                convex in the discount rate, so the upside stretches further — the grid below
                shows where.
              </p>
            )}

            {base?.terminal_value_share != null && (
              <p className="text-[10px] leading-relaxed text-muted-foreground/60">
                <span className="text-amber-400/90">
                  {percent(base.terminal_value_share, 0)} of the base case is terminal value
                </span>{' '}
                — the part that assumes a growth rate forever, at an implied exit of{' '}
                {base.implied_exit_fcf_multiple?.toFixed(0)}× free cash flow. Treat the band as a
                range of opinions, not a measurement.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* What it is built from */}
      <Card>
        <CardContent className="flex flex-col gap-3 py-4">
          <div className="flex items-baseline justify-between">
            <span className="text-[13px] font-semibold text-foreground">Free cash flow</span>
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
              {inputs?.fcf_source === 'statement' ? 'from the cash flow statement' : 'summary field'}
            </span>
          </div>
          <Separator />

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div>
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60">Latest</p>
              <p className="text-sm font-semibold tabular-nums text-foreground">
                {compact(inputs?.fcf_ttm)}
              </p>
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60">Median</p>
              <p className="text-sm font-semibold tabular-nums text-foreground">
                {compact(inputs?.fcf_median)}
              </p>
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                Year-to-year spread
              </p>
              <p className="text-sm font-semibold tabular-nums text-foreground">
                {inputs?.fcf_dispersion == null ? '—' : `${(inputs.fcf_dispersion * 100).toFixed(0)}%`}
              </p>
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                Net debt
              </p>
              <p className="text-sm font-semibold tabular-nums text-foreground">
                {compact(inputs?.net_debt)}
              </p>
            </div>
          </div>

          {(inputs?.fcf_history?.length ?? 0) > 1 && (
            <p className="text-[10px] tabular-nums text-muted-foreground/60">
              By year, newest first: {inputs!.fcf_history!.map(v => compact(v)).join(' · ')}
              {(inputs?.fcf_dispersion ?? 0) > 0.5 &&
                ' — wide enough that which year you start from changes the answer materially.'}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Sensitivity */}
      {sensitivity && (
        <Card>
          <CardContent className="flex flex-col gap-3 py-4">
            <div className="flex items-baseline justify-between">
              <span className="text-[13px] font-semibold text-foreground">
                Sensitivity — value per share
              </span>
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                discount rate × terminal growth
              </span>
            </div>
            <Separator />
            <SensitivityGrid grid={sensitivity} price={price} />
          </CardContent>
        </Card>
      )}

      <ExplainCard symbol={symbol} params={params} ready={!!base?.value_per_share} />

      <p className="px-1 text-[10px] leading-relaxed text-muted-foreground/50">
        A discounted cash flow is an opinion with arithmetic attached, not a measurement. Inputs are
        from yfinance and the company's own filings; the output moves by tens of percent on
        assumptions nobody can observe, which is why the band and the grid are here. Not investment
        advice.
        {inputs?.revenue_growth != null && (
          <> Derived growth anchor: revenue {signedPercent(inputs.revenue_growth)} year on year.</>
        )}
      </p>
    </div>
  )
}
