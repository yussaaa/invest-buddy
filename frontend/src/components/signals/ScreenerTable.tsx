/**
 * One preset screen. Relative volume is the column worth reading first —
 * it is the cheapest honest sign that something is happening — and the
 * Reddit column says whether the crowd has noticed.
 */

import { Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { ScreenRow } from '@/lib/types'
import { CAP_TIERS, YEAR_BANDS, capTier, compact, relVolume, signedPct, yearChangeBand } from './format'

export default function ScreenerTable({
  rows,
  loading,
  selected,
  onSelect,
}: {
  rows: ScreenRow[]
  loading: boolean
  selected: string | null
  onSelect: (symbol: string) => void
}) {
  if (loading && rows.length === 0) {
    return (
      <div className="flex items-center gap-2 py-10 text-xs text-muted-foreground">
        <Loader2 size={14} className="animate-spin" />
        Running the screen…
      </div>
    )
  }
  if (rows.length === 0) {
    return <p className="py-10 text-center text-xs text-muted-foreground/60">Nothing passes this screen right now.</p>
  }

  return (
    <div className="overflow-x-auto">
      {/* The ticker column takes the leftover width and truncates, so the
          numeric columns always fit the card rather than hiding behind a
          horizontal scrollbar macOS doesn't show. */}
      <table className="w-full min-w-[460px] whitespace-nowrap text-left text-[12px]">
        <thead className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
          <tr className="border-b border-border/60">
            <th className="w-full py-2 pr-3 font-medium">Ticker</th>
            <th className="py-2 pr-3 text-right font-medium">Last</th>
            <th className="py-2 pr-3 text-right font-medium">Chg</th>
            <th className="py-2 pr-3 text-right font-medium" title="Today's volume ÷ 3-month average">Rel vol</th>
            <th className="py-2 pr-3 text-right font-medium" title="Colour is the size tier — see the key below">Mkt cap</th>
            <th className="py-2 pr-3 text-right font-medium" title="Price change over the last 52 weeks">52w</th>
            <th className="py-2 text-right font-medium" title="Rank on the Reddit mention board, if on it">Reddit</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const rv = relVolume(row.rel_volume)
            const up = (row.change_percent ?? 0) >= 0
            const tier = capTier(row.market_cap)
            const year = yearChangeBand(row.week52_change_percent)
            return (
              <tr
                key={row.symbol}
                onClick={() => onSelect(row.symbol)}
                className={cn(
                  'cursor-pointer border-b border-border/30 transition-colors last:border-b-0 hover:bg-muted/40',
                  selected === row.symbol && 'bg-primary/10 hover:bg-primary/10',
                )}
              >
                <td className="max-w-0 py-1.5 pr-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="shrink-0 font-mono font-semibold text-foreground">{row.symbol}</span>
                    {row.sector && (
                      <Badge variant="secondary" title={row.sector}
                             className="block min-w-0 truncate px-1.5 py-0 text-[9px] font-medium uppercase tracking-wide">
                        {row.sector}
                      </Badge>
                    )}
                  </div>
                  <p className="truncate text-[11px] text-muted-foreground/70" title={row.name}>{row.name}</p>
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-foreground">
                  {row.last?.toFixed(2) ?? '—'}
                </td>
                <td className={cn('py-1.5 pr-3 text-right font-medium tabular-nums', up ? 'text-emerald-400' : 'text-rose-400')}>
                  {signedPct(row.change_percent, 2)}
                </td>
                <td className={cn('py-1.5 pr-3 text-right tabular-nums', rv.emphasis ? 'font-semibold text-sky-300' : 'text-muted-foreground')}>
                  {rv.text}
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums">
                  {tier ? (
                    <span className={cn('rounded px-1.5 py-0.5', tier.className)}
                          title={`${tier.label} cap (${tier.range})`}>
                      ${compact(row.market_cap)}
                    </span>
                  ) : (
                    <span className="text-muted-foreground/40">—</span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums">
                  {year ? (
                    <span className={cn('rounded px-1.5 py-0.5', year.className)}
                          title={`Over 52 weeks: ${year.label}`}>
                      {signedPct(row.week52_change_percent)}
                    </span>
                  ) : (
                    <span className="text-muted-foreground/40">—</span>
                  )}
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  {row.social ? (
                    <span className="rounded border border-sky-500/30 bg-sky-500/10 px-1.5 text-[10px] text-sky-300"
                          title={`Mentions ${signedPct(row.social.mention_change_pct)} vs 24h`}>
                      #{row.social.rank}
                    </span>
                  ) : (
                    <span className="text-muted-foreground/40">—</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 whitespace-normal border-t border-border/30 pt-2 text-[10px] text-muted-foreground/60">
        <span className="flex flex-wrap items-center gap-1">
          Size
          {CAP_TIERS.map(t => (
            <span key={t.id} className={cn('rounded px-1.5', t.className)} title={t.range}>{t.label}</span>
          ))}
        </span>
        <span className="flex flex-wrap items-center gap-1">
          52w
          {[...YEAR_BANDS].reverse().map(b => (
            <span key={b.label} className={cn('rounded px-1.5', b.className)}>{b.label}</span>
          ))}
        </span>
        <span>Colours describe size and past moves; a deep fall can be a setup or a falling knife.</span>
      </div>
    </div>
  )
}
