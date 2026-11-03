/**
 * The Reddit mention board — most-discussed tickers, with how that changed.
 *
 * A row is a reading, not a verdict: rank, mentions, the change against a day
 * ago, and (once there is history) a z-score against the ticker's own
 * baseline. Badges come from the deterministic attention rules.
 */

import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TrendingRow } from '@/lib/types'
import SignalBadge from './SignalBadge'
import { attentionLabel, compact, rankDelta, signedPct } from './format'

const TONE = {
  up: 'text-emerald-400',
  down: 'text-rose-400',
  flat: 'text-muted-foreground/60',
  new: 'text-sky-300',
} as const

export default function SocialTrendingTable({
  rows,
  loading,
  selected,
  onSelect,
}: {
  rows: TrendingRow[]
  loading: boolean
  selected: string | null
  onSelect: (symbol: string) => void
}) {
  if (loading && rows.length === 0) {
    return (
      <div className="flex items-center gap-2 py-10 text-xs text-muted-foreground">
        <Loader2 size={14} className="animate-spin" />
        Reading the board…
      </div>
    )
  }
  if (rows.length === 0) {
    return <p className="py-10 text-center text-xs text-muted-foreground/60">No rows from this source.</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-left text-[12px]">
        <thead className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
          <tr className="border-b border-border/60">
            <th className="w-8 py-2 pr-2 text-right font-medium">#</th>
            <th className="w-12 py-2 pr-3 text-right font-medium" title="Places moved in 24h">Δ</th>
            <th className="py-2 pr-3 font-medium">Ticker</th>
            <th className="py-2 pr-3 text-right font-medium" title="Mentions in the last 24h">Mentions</th>
            <th className="py-2 pr-3 text-right font-medium" title="Change vs the previous 24h">vs 24h</th>
            <th className="py-2 text-right font-medium" title="Against this ticker's own history">Attention</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const delta = rankDelta(row.rank_change)
            const attention = attentionLabel(row.attention_z, row.history_days)
            return (
              <tr
                key={row.symbol}
                onClick={() => onSelect(row.symbol)}
                className={cn(
                  'cursor-pointer border-b border-border/30 transition-colors last:border-b-0 hover:bg-muted/40',
                  selected === row.symbol && 'bg-primary/10 hover:bg-primary/10',
                )}
              >
                <td className="py-1.5 pr-2 text-right tabular-nums text-muted-foreground/50">{row.rank}</td>
                <td className={cn('py-1.5 pr-3 text-right text-[11px] tabular-nums', TONE[delta.tone])}>
                  {delta.text}
                </td>
                <td className="py-1.5 pr-3">
                  {/* Badges sit with the name rather than in a last column,
                      which is the first thing a narrow card would clip. */}
                  <div className="flex max-w-[300px] items-center gap-1.5">
                    <span className="shrink-0 font-mono font-semibold text-foreground">{row.symbol}</span>
                    {row.signal_ids.map(id => <SignalBadge key={id} id={id} />)}
                    <span className="truncate text-[11px] text-muted-foreground/70" title={row.name}>{row.name}</span>
                  </div>
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-foreground">{compact(row.mentions)}</td>
                <td
                  className={cn(
                    'py-1.5 pr-3 text-right tabular-nums',
                    (row.mention_change_pct ?? 0) > 0 ? 'text-emerald-400/90' : 'text-muted-foreground',
                  )}
                >
                  {signedPct(row.mention_change_pct)}
                </td>
                <td
                  title={attention.title}
                  className={cn(
                    'py-1.5 text-right tabular-nums',
                    attention.emphasis ? 'font-semibold text-sky-300' : 'text-muted-foreground/70',
                  )}
                >
                  {attention.text}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
