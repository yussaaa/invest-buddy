/**
 * A ticker on the wire: symbol, the latest session's move, and its industry.
 *
 * The move is context, not a verdict: the latest session's change against the
 * close before it — before the open that is still yesterday — and never the
 * reaction to this headline (that is measured separately, after the fact).
 * The industry says what kind of company it is without opening the chart;
 * the sector is in the tooltip.
 */

import { cn } from '@/lib/utils'
import type { Quote, SymbolInfo } from '@/lib/types'

export default function TickerChip({
  symbol,
  quote,
  info,
  onClick,
}: {
  symbol: string
  quote?: Quote
  info?: SymbolInfo | null
  onClick?: (symbol: string) => void
}) {
  const pct = quote?.change_percent
  const tone = pct === undefined ? 'text-muted-foreground' : pct > 0 ? 'text-emerald-400' : pct < 0 ? 'text-rose-400' : 'text-muted-foreground'
  const title = [
    symbol,
    info?.sector,
    info?.industry,
    pct !== undefined ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}% on the latest session` : null,
  ].filter(Boolean).join(' · ')
  return (
    <button
      type="button"
      onClick={event => {
        event.stopPropagation()
        onClick?.(symbol)
      }}
      title={title}
      className="inline-flex min-w-0 max-w-full items-center gap-1 rounded border border-border bg-muted/40 px-1.5 py-px font-mono text-[11px] text-foreground/90 hover:border-primary/50 hover:text-primary"
    >
      {symbol}
      {pct !== undefined && (
        <span className={cn('tabular-nums', tone)}>
          {pct >= 0 ? '+' : ''}
          {pct.toFixed(1)}%
        </span>
      )}
      {info?.industry && (
        <span className="min-w-0 max-w-[14rem] truncate border-l border-border pl-1 font-sans text-[10.5px] text-muted-foreground">
          {info.industry}
        </span>
      )}
    </button>
  )
}
