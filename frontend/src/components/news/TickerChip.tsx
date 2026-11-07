/**
 * A ticker on the wire, with today's move beside it when we have a quote.
 *
 * The move is context, not a verdict: the latest session's change against the
 * close before it — before the open that is still yesterday — and never the
 * reaction to this headline (that is measured separately, after the fact).
 */

import { cn } from '@/lib/utils'
import type { Quote } from '@/lib/types'

export default function TickerChip({
  symbol,
  quote,
  onClick,
}: {
  symbol: string
  quote?: Quote
  onClick?: (symbol: string) => void
}) {
  const pct = quote?.change_percent
  const tone = pct === undefined ? 'text-muted-foreground' : pct > 0 ? 'text-emerald-400' : pct < 0 ? 'text-rose-400' : 'text-muted-foreground'
  return (
    <button
      type="button"
      onClick={event => {
        event.stopPropagation()
        onClick?.(symbol)
      }}
      title={pct === undefined ? symbol : `${symbol} ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}% on the latest session`}
      className="inline-flex items-center gap-1 rounded border border-border bg-muted/40 px-1.5 py-px font-mono text-[11px] text-foreground/90 hover:border-primary/50 hover:text-primary"
    >
      {symbol}
      {pct !== undefined && (
        <span className={cn('tabular-nums', tone)}>
          {pct >= 0 ? '+' : ''}
          {pct.toFixed(1)}%
        </span>
      )}
    </button>
  )
}
