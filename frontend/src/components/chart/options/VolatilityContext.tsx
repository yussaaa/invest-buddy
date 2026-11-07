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
import type { OptionExpirySummary, SkewPoint, VolatilityContext as VolContext } from '@/lib/types'
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

/** IV of the skew point nearest a target moneyness — null if the curve is empty. */
function ivNearMoneyness(points: SkewPoint[], target: number): number | null {
  if (!points.length) return null
  return points.reduce((best, p) =>
    Math.abs(p.moneyness - target) < Math.abs(best.moneyness - target) ? p : best
  ).iv
}

/** A small line of implied vol across moneyness — the skew for one expiry. */
function SkewSparkline({ points }: { points: SkewPoint[] }) {
  const W = 180
  const H = 40
  const PAD = 3
  const ms = points.map(p => p.moneyness)
  const ivs = points.map(p => p.iv)
  const minM = Math.min(...ms)
  const maxM = Math.max(...ms)
  const minV = Math.min(...ivs)
  const maxV = Math.max(...ivs)
  const x = (m: number) => PAD + ((m - minM) / (maxM - minM || 1)) * (W - 2 * PAD)
  const y = (v: number) => PAD + (1 - (v - minV) / (maxV - minV || 1)) * (H - 2 * PAD)
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.moneyness).toFixed(1)},${y(p.iv).toFixed(1)}`).join(' ')
  const atmX = minM <= 1 && maxM >= 1 ? x(1) : null
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }} preserveAspectRatio="none">
      {atmX != null && (
        <line
          x1={atmX}
          y1={PAD}
          x2={atmX}
          y2={H - PAD}
          className="stroke-muted-foreground/25"
          strokeDasharray="2 2"
        />
      )}
      <path d={d} fill="none" className="stroke-sky-400" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
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
  const callVol = expiries.reduce((sum, e) => sum + (e.total_call_volume ?? 0), 0)
  const putVol = expiries.reduce((sum, e) => sum + (e.total_put_volume ?? 0), 0)

  // The skew is per expiry; show the nearest one that has a real curve — the
  // front month is the most liquid and the most watched.
  const skewExpiry = expiries
    .filter(e => (e.skew?.length ?? 0) >= 2)
    .sort((a, b) => a.dte - b.dte)[0]
  const skewPts = skewExpiry?.skew ?? []
  // Downside (~90% strike puts) minus upside (~110% strike calls) IV: the sign
  // is the skew. Read near ±10%, not the extreme wings, whose IV is the noisy
  // part of the smile. Equities usually bid downside, so it reads positive.
  const downIv = ivNearMoneyness(skewPts, 0.9)
  const upIv = ivNearMoneyness(skewPts, 1.1)
  const skewSpread = downIv != null && upIv != null ? downIv - upIv : null
  const skewLabel =
    skewSpread == null
      ? '—'
      : skewSpread > 0.02
        ? 'downside bid'
        : skewSpread < -0.02
          ? 'upside bid'
          : 'flat'

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
            label="Put / call vol"
            value={callVol > 0 ? fmt(putVol / callVol) : '—'}
          />
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

      <Card>
        <CardContent className="flex flex-col gap-2.5 py-4">
          <div className="flex items-baseline justify-between">
            <span className="text-[13px] font-semibold text-foreground">IV skew</span>
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
              {skewExpiry ? `${skewExpiry.dte}d · OTM` : 'OTM'}
            </span>
          </div>

          {skewPts.length < 2 ? (
            <p className="py-3 text-xs text-muted-foreground/60">No skew data.</p>
          ) : (
            <>
              <div className="flex items-baseline gap-2">
                <span className="text-lg font-bold capitalize tabular-nums text-foreground">
                  {skewLabel}
                </span>
                {skewSpread != null && (
                  <span className="text-[11px] tabular-nums text-muted-foreground">
                    {pct(Math.abs(skewSpread), 1)} put−call
                  </span>
                )}
              </div>
              <SkewSparkline points={skewPts} />
              <div className="flex justify-between text-[9px] text-muted-foreground/50">
                <span>downside puts</span>
                <span>ATM</span>
                <span>upside calls</span>
              </div>
              <p className="text-[10px] leading-snug text-muted-foreground/60">
                Implied vol across strikes for the front expiry — a steeper left side
                means the market pays up for downside protection.
              </p>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
