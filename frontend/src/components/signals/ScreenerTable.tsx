/**
 * One preset screen. Relative volume is the column worth reading first —
 * it is the cheapest honest sign that something is happening — and the
 * Reddit column says whether the crowd has noticed.
 */

import { Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { ScreenRow } from '@/lib/types'
import { compact, relVolume, signedPct } from './format'

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
      <table className="w-full min-w-[560px] text-left text-[12px]">
        <thead className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
          <tr className="border-b border-border/60">
            <th className="py-2 pr-3 font-medium">Ticker</th>
            <th className="py-2 pr-3 text-right font-medium">Last</th>
            <th className="py-2 pr-3 text-right font-medium">Chg</th>
            <th className="py-2 pr-3 text-right font-medium" title="Today's volume ÷ 3-month average">Rel vol</th>
            <th className="py-2 pr-3 text-right font-medium">Mkt cap</th>
            <th className="hidden py-2 pr-3 text-right font-medium 2xl:table-cell">52w</th>
            <th className="py-2 text-right font-medium" title="Rank on the Reddit mention board, if on it">Reddit</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const rv = relVolume(row.rel_volume)
            const up = (row.change_percent ?? 0) >= 0
            return (
              <tr
                key={row.symbol}
                onClick={() => onSelect(row.symbol)}
                className={cn(
                  'cursor-pointer border-b border-border/30 transition-colors last:border-b-0 hover:bg-muted/40',
                  selected === row.symbol && 'bg-primary/10 hover:bg-primary/10',
                )}
              >
                <td className="py-1.5 pr-3">
                  <div className="flex max-w-[260px] items-center gap-2">
                    <span className="font-mono font-semibold text-foreground">{row.symbol}</span>
                    {row.sector && (
                      <Badge variant="secondary" className="shrink-0 px-1.5 py-0 text-[9px] font-medium uppercase tracking-wide">
                        {row.sector}
                      </Badge>
                    )}
                  </div>
                  <p className="max-w-[260px] truncate text-[11px] text-muted-foreground/70" title={row.name}>{row.name}</p>
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
                <td className="py-1.5 pr-3 text-right tabular-nums text-muted-foreground">${compact(row.market_cap)}</td>
                <td className="hidden py-1.5 pr-3 text-right tabular-nums text-muted-foreground 2xl:table-cell">
                  {signedPct(row.week52_change_percent)}
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
    </div>
  )
}
