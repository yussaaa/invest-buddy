/**
 * Portfolio risk for a watchlist: per-name beta and volatility, the pairwise
 * correlation matrix, and the equal-weight aggregate. Watchlists carry no
 * weights, so this is an equal-weight read — enough to see where the basket is
 * exposed (beta), how much it moves (vol) and whether the names are really
 * diversified or just the same bet under different tickers (correlation).
 *
 * Lazily mounted from the watchlist card, so it only fetches when opened.
 */

import { Fragment } from 'react'
import { AlertCircle, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { K, TTL } from '@/lib/cacheKeys'
import { useCachedResource } from '@/hooks/useCachedResource'

function num2(v?: number | null): string {
  return v == null ? '—' : v.toFixed(2)
}

function pct1(v?: number | null): string {
  return v == null ? '—' : `${(v * 100).toFixed(1)}%`
}

/** Rose for positive correlation, sky for negative, alpha by magnitude. */
function corrColor(v: number | null): string {
  if (v == null) return 'transparent'
  const a = Math.min(Math.abs(v), 1) * 0.85
  return v >= 0 ? `rgba(244,63,94,${a})` : `rgba(56,189,248,${a})`
}

function Stat({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60">{label}</p>
      <p className="mt-0.5 text-lg font-bold tabular-nums capitalize text-foreground">{value}</p>
      {detail && <p className="text-[10px] text-muted-foreground/60">{detail}</p>}
    </div>
  )
}

export default function PortfolioRiskPanel({ tickers }: { tickers: string[] }) {
  const res = useCachedResource(
    K.portfolioRisk(tickers),
    () => api.market.portfolioRisk(tickers),
    { ttl: TTL.portfolioRisk, shouldCache: r => r.available === true },
  )
  const d = res.data

  if (res.isLoading && !d) {
    return (
      <div className="flex items-center gap-2 py-6 text-xs text-muted-foreground">
        <Loader2 size={14} className="animate-spin" />
        Measuring portfolio risk…
      </div>
    )
  }

  const error = res.error?.message ?? (d && !d.available ? d.reason : null)
  if (error) {
    return (
      <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
        <AlertCircle size={14} />
        {error}
      </div>
    )
  }
  if (!d?.available || !d.portfolio || !d.correlation) return null

  const p = d.portfolio
  const syms = d.correlation.symbols
  const matrix = d.correlation.matrix

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-3">
        <Stat label="Portfolio beta" value={num2(p.beta)} detail="vs SPY · equal-weight" />
        <Stat label="Volatility" value={pct1(p.vol)} detail="annualised" />
        <Stat
          label="Diversification"
          value={p.diversification}
          detail={`avg corr ${num2(p.avg_correlation)}`}
        />
      </div>

      {p.most_correlated && (
        <p className="text-[11px] text-muted-foreground">
          Most correlated:{' '}
          <span className="text-foreground">
            {p.most_correlated.a} &amp; {p.most_correlated.b}
          </span>{' '}
          at {num2(p.most_correlated.value)} — they move largely together.
        </p>
      )}

      {/* Correlation heatmap: rose = move together, sky = move apart. */}
      <div className="overflow-x-auto">
        <div
          className="inline-grid gap-0.5"
          style={{ gridTemplateColumns: `auto repeat(${syms.length}, 26px)` }}
        >
          <span />
          {syms.map(s => (
            <span key={s} className="truncate text-center text-[9px] text-muted-foreground/70">
              {s}
            </span>
          ))}
          {syms.map((rowSym, i) => (
            <Fragment key={rowSym}>
              <span className="whitespace-nowrap pr-1.5 text-right text-[9px] leading-[26px] text-muted-foreground/70">
                {rowSym}
              </span>
              {syms.map((colSym, j) => {
                const v = matrix[i]?.[j] ?? null
                const diag = i === j
                return (
                  <div
                    key={colSym}
                    title={`${rowSym} · ${colSym}: ${num2(v)}`}
                    className="flex h-[26px] items-center justify-center rounded-sm text-[9px] tabular-nums"
                    style={{ background: diag ? 'transparent' : corrColor(v) }}
                  >
                    {diag ? (
                      <span className="text-muted-foreground/25">–</span>
                    ) : (
                      <span className="text-foreground/90">{v == null ? '' : Math.round(v * 100)}</span>
                    )}
                  </div>
                )
              })}
            </Fragment>
          ))}
        </div>
      </div>

      {/* Per-name beta and volatility. */}
      <div
        className="grid gap-x-3 gap-y-0.5 text-[11px] tabular-nums"
        style={{ gridTemplateColumns: '1fr auto auto' }}
      >
        <span className="text-[9px] uppercase tracking-wide text-muted-foreground/50">name</span>
        <span className="text-right text-[9px] uppercase tracking-wide text-muted-foreground/50">β</span>
        <span className="text-right text-[9px] uppercase tracking-wide text-muted-foreground/50">vol</span>
        {(d.names ?? []).map(n => (
          <Fragment key={n.symbol}>
            <span className="text-muted-foreground">{n.symbol}</span>
            <span className="text-right text-foreground">{num2(n.beta)}</span>
            <span className="text-right text-foreground">{pct1(n.vol)}</span>
          </Fragment>
        ))}
      </div>

      {d.missing && d.missing.length > 0 && (
        <p className="text-[10px] text-muted-foreground/50">No price data for: {d.missing.join(', ')}</p>
      )}
      <p className="text-[9px] leading-snug text-muted-foreground/50">
        Beta vs SPY and correlations from ~{d.window_sessions} daily returns, equal-weighted.
        A measure of exposure, not a recommendation.
      </p>
    </div>
  )
}
