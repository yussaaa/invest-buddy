/**
 * Trend and extension — which way the averages are going, and how far price
 * has run from the 200-day one.
 *
 * Default-exported and loaded lazily, so recharts ships in its own chunk that a
 * reader who leaves this section closed never downloads.
 */

import { AlertCircle, Loader2 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import { K, TTL } from '@/lib/cacheKeys'
import { useCachedResource } from '@/hooks/useCachedResource'
import { zipTrend } from '@/components/charts/zip'
import { MA_COLORS } from '@/components/charts/theme'
import type { SlopeLabel, ZLabel } from '@/lib/types'
import PriceBandChart from './PriceBandChart'
import SlopeChart from './SlopeChart'
import ZScoreChart from './ZScoreChart'
import { useMemo } from 'react'

export const TREND_SUBLABEL =
  'SMA 20/50/200 · 21-session geometric slope, annualised · z-score and ±1.5σ vs the 200 DMA'

function fmt(v?: number | null, digits = 2): string {
  return v == null ? '—' : v.toFixed(digits)
}

function signed(v?: number | null, digits = 2): string {
  return v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`
}

function tone(v?: number | null): string {
  if (v == null || v === 0) return 'text-muted-foreground'
  return v > 0 ? 'text-emerald-400' : 'text-rose-400'
}

const SLOPE_WORDS: Record<SlopeLabel, string> = {
  strong_uptrend: 'strong uptrend',
  uptrend: 'uptrend',
  weak_uptrend: 'weak uptrend',
  flat: 'flat',
  weak_downtrend: 'weak downtrend',
  downtrend: 'downtrend',
  strong_downtrend: 'strong downtrend',
}

const Z_WORDS: Record<ZLabel, string> = {
  extended_high: 'extended',
  elevated: 'elevated',
  neutral: 'neutral',
  depressed: 'depressed',
  extended_low: 'extended low',
}

function Stat({
  label,
  value,
  detail,
  badge,
  badgeTone,
}: {
  label: string
  value: string
  detail?: string
  badge?: string
  badgeTone?: string
}) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60">{label}</p>
      <p className="mt-0.5 text-lg font-bold tabular-nums text-foreground">{value}</p>
      <div className="mt-0.5 flex items-center gap-1.5">
        {detail && <span className={cn('text-[11px] tabular-nums', badgeTone)}>{detail}</span>}
        {badge && (
          <Badge variant="secondary" className="px-1.5 py-0 text-[9px] uppercase tracking-wide">
            {badge}
          </Badge>
        )}
      </div>
    </div>
  )
}

/** The colour key, since three lines share every chart. */
function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-3 px-1 text-[10px] text-muted-foreground/70">
      {[20, 50, 200].map(w => (
        <span key={w} className="flex items-center gap-1.5">
          <span className="h-[2px] w-3 rounded" style={{ background: MA_COLORS[w] }} />
          {w}-day SMA
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="h-2 w-3 rounded bg-muted-foreground/20" />
        ±1.5σ band
      </span>
    </div>
  )
}

export default function TrendPanel({ symbol }: { symbol: string }) {
  const res = useCachedResource(
    K.trend(symbol, '2y'),
    () => api.market.trend(symbol, '2y'),
    { ttl: TTL.trend },
  )

  const rows = useMemo(() => zipTrend(res.data?.series), [res.data])

  if (res.isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
          <Loader2 size={16} className="animate-spin" />
          Measuring trend for {symbol}…
        </CardContent>
      </Card>
    )
  }

  const error = res.error?.message ?? res.data?.error
  if (error) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <AlertCircle size={15} />
          {error}
        </CardContent>
      </Card>
    )
  }

  const summary = res.data?.summary
  const slope200 = summary?.slopes?.['200']

  return (
    <div className="flex flex-col gap-3">
      <Card>
        <CardContent className="grid grid-cols-2 gap-4 py-4 sm:grid-cols-3 lg:grid-cols-5">
          <Stat label="Price" value={fmt(summary?.price)} />
          <Stat
            label={`${summary?.reference_window ?? 200} DMA / distance`}
            value={fmt(summary?.sma)}
            detail={`${signed(summary?.distance_percent)}%`}
            badgeTone={tone(summary?.distance_percent)}
          />
          <Stat
            label="Z-score"
            value={fmt(summary?.z_score, 2)}
            badge={summary?.z_label ? Z_WORDS[summary.z_label] : undefined}
          />
          <Stat
            label={`${summary?.reference_window ?? 200} DMA slope (ann.)`}
            value={`${signed(slope200?.annualised_percent, 1)}%`}
            badge={slope200?.label ? SLOPE_WORDS[slope200.label] : undefined}
            badgeTone={tone(slope200?.annualised_percent)}
          />
          <Stat label="Z-score band" value={summary?.z_band ?? '—'} />
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex flex-col gap-2 py-4">
          <div className="flex items-baseline justify-between">
            <span className="text-[13px] font-semibold text-foreground">
              Price, moving averages &amp; ±{summary?.band_sigma ?? 1.5}σ band
            </span>
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
              {summary?.sessions ? `${rows.length} of ${summary.sessions} sessions` : ''}
            </span>
          </div>
          <PriceBandChart rows={rows} />
          <Legend />
        </CardContent>
      </Card>

      <div className="grid gap-3 xl:grid-cols-2">
        <Card>
          <CardContent className="flex flex-col gap-2 py-4">
            <div className="flex items-baseline justify-between">
              <span className="text-[13px] font-semibold text-foreground">SMA slope</span>
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                % per day
              </span>
            </div>
            <SlopeChart rows={rows} />
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-2 py-4">
            <div className="flex items-baseline justify-between">
              <span className="text-[13px] font-semibold text-foreground">
                Z-score vs {summary?.reference_window ?? 200} DMA
              </span>
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                ±{summary?.band_sigma ?? 1.5} marked
              </span>
            </div>
            <ZScoreChart rows={rows} threshold={summary?.band_sigma ?? 1.5} />
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
