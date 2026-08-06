/**
 * The volatility block: is premium expensive right now, and by what measure?
 *
 * The gauge label is the important part. Under the free data provider there is
 * no implied-volatility history to rank against, so this shows where today's
 * IV sits inside a year of the stock's own *realized* volatility and says so.
 * When a provider that has real IV history is configured, the same component
 * renders a true 52-week IV rank instead — driven entirely by the
 * `iv_rank_available` flag on the payload, so nothing here needs to know which
 * provider is running.
 */

import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type { OptionExpirySummary, VolatilityContext as VolContext } from '@/lib/types'
import { compact, fmt, pct, pts, shortDate } from './format'

/** 0–100 track with a marker, matching the RSI gauge in TechnicalPanel. */
function PercentileGauge({ value }: { value: number }) {
  return (
    <div className="relative h-2 w-full rounded-full bg-muted">
      <div className="absolute left-0 top-0 h-full w-[30%] rounded-l-full bg-sky-500/20" />
      <div className="absolute right-0 top-0 h-full w-[30%] rounded-r-full bg-amber-500/20" />
      <div
        className="absolute top-1/2 h-3 w-[3px] -translate-y-1/2 rounded-full bg-foreground"
        style={{ left: `calc(${Math.max(0, Math.min(100, value))}% - 1.5px)` }}
      />
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums text-foreground">{value}</span>
    </div>
  )
}

interface Props {
  volatility?: VolContext
  expiries?: OptionExpirySummary[]
  nextEarnings?: string | null
  shortestDte?: number
}

export default function VolatilityContextCards({
  volatility,
  expiries = [],
  nextEarnings,
  shortestDte,
}: Props) {
  const hasRank = volatility?.iv_rank_available === true
  const gaugeValue = hasRank ? volatility?.iv_rank : volatility?.iv_percentile_vs_realized
  const gaugeLabel = hasRank ? 'IV rank (52w)' : 'IV vs realized'

  // The ratio is the headline reading: above 1 means the market is charging
  // more than the stock has actually been moving.
  const ratio = volatility?.iv_hv_ratio

  const callOi = expiries.reduce((sum, e) => sum + (e.total_call_oi ?? 0), 0)
  const putOi = expiries.reduce((sum, e) => sum + (e.total_put_oi ?? 0), 0)

  // Earnings inside the shortest expiry is the single largest unmodelled risk
  // in short-dated premium selling, so it gets colour rather than a footnote.
  const earningsSoon =
    nextEarnings != null &&
    shortestDte != null &&
    (new Date(`${nextEarnings.slice(0, 10)}T00:00:00`).getTime() - Date.now()) /
      86_400_000 <=
      shortestDte

  return (
    <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
      <Card>
        <CardContent className="flex flex-col gap-2.5 py-4">
          <div className="flex items-baseline justify-between">
            <span className="text-[13px] font-semibold text-foreground">Implied volatility</span>
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
              30-day ATM
            </span>
          </div>
          <span className="text-2xl font-bold tabular-nums text-foreground">
            {pct(volatility?.atm_iv_30d)}
          </span>
          <Stat label="vs realized (20d)" value={ratio != null ? `${fmt(ratio)}×` : '—'} />
          <p className="text-[10px] leading-snug text-muted-foreground/60">
            {ratio == null
              ? 'Not enough data to compare.'
              : ratio >= 1
                ? 'Options are pricing more movement than the stock has delivered.'
                : 'Options are pricing less movement than the stock has delivered.'}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex flex-col gap-2.5 py-4">
          <div className="flex items-baseline justify-between">
            <span className="text-[13px] font-semibold text-foreground">Realized volatility</span>
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
              annualised
            </span>
          </div>
          <Stat label="20 sessions" value={pct(volatility?.hv_20)} />
          <Stat label="60 sessions" value={pct(volatility?.hv_60)} />
          <Stat label="252 sessions" value={pct(volatility?.hv_252)} />
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex flex-col gap-2.5 py-4" title={volatility?.note}>
          <div className="flex items-baseline justify-between">
            <span className="text-[13px] font-semibold text-foreground">{gaugeLabel}</span>
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
              {hasRank ? '52 weeks' : 'proxy'}
            </span>
          </div>

          {gaugeValue == null ? (
            <p className="py-3 text-xs text-muted-foreground/60">
              {volatility?.note ?? 'No volatility history available.'}
            </p>
          ) : (
            <>
              <span className="text-2xl font-bold tabular-nums text-foreground">
                {pts(gaugeValue)}
                <span className="ml-1 text-sm font-normal text-muted-foreground">/ 100</span>
              </span>
              <PercentileGauge value={gaugeValue} />
              <div className="flex justify-between text-[9px] text-muted-foreground/50">
                <span>cheap</span>
                <span>expensive</span>
              </div>
              {!hasRank && (
                <p className="text-[10px] leading-snug text-muted-foreground/60">
                  Not an IV rank — no provider of implied-volatility history is configured.
                  This places today&apos;s IV inside a year of this stock&apos;s own realized
                  volatility.
                </p>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex flex-col gap-2.5 py-4">
          <div className="flex items-baseline justify-between">
            <span className="text-[13px] font-semibold text-foreground">Chain</span>
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
              {expiries.length} expir{expiries.length === 1 ? 'y' : 'ies'}
            </span>
          </div>
          <Stat
            label="Put / call OI"
            value={callOi > 0 ? fmt(putOi / callOi) : '—'}
          />
          <Stat label="Total open interest" value={compact(callOi + putOi)} />
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">Next earnings</span>
            {nextEarnings ? (
              <Badge
                variant="secondary"
                className={cn('text-[10px]', earningsSoon && 'text-rose-400')}
              >
                {shortDate(nextEarnings)}
              </Badge>
            ) : (
              <span className="tabular-nums text-muted-foreground">—</span>
            )}
          </div>
          {earningsSoon && (
            <p className="text-[10px] leading-snug text-rose-400/80">
              Earnings land inside the shortest expiry screened. None of the probabilities
              below model that event.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
