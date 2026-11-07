/**
 * One line of the wire: time, category, headline, tickers.
 *
 * Dense on purpose — a wire is scanned, not read. The row that just arrived
 * over the stream flashes once so the eye finds it.
 */

import { cn } from '@/lib/utils'
import type { NewsHeadline, Quote } from '@/lib/types'
import { CATEGORY_META, SOURCE_LABEL, etTime } from './format'
import TickerChip from './TickerChip'

// Categories that move the whole tape, or one name hard, get a heavier title.
const LOUD = new Set(['monetary_policy', 'earnings', 'deal', 'accounting'])

export default function HeadlineRow({
  headline,
  quotes,
  selected,
  fresh,
  onSelect,
  onTicker,
}: {
  headline: NewsHeadline
  quotes: Record<string, Quote>
  selected: boolean
  fresh: boolean
  onSelect: (id: string) => void
  onTicker: (symbol: string) => void
}) {
  const category = headline.category ? CATEGORY_META[headline.category] : null
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(headline.id)}
      onKeyDown={event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect(headline.id)
        }
      }}
      aria-selected={selected}
      className={cn(
        'group grid cursor-pointer grid-cols-[3rem_minmax(0,1fr)] gap-x-3 border-b border-border/60 px-3 py-2 text-left outline-none transition-colors',
        'hover:bg-muted/40 focus-visible:bg-muted/50',
        selected && 'bg-primary/10 hover:bg-primary/10',
        fresh && 'animate-wireflash',
      )}
    >
      <time
        dateTime={headline.published_at}
        className="pt-px font-mono text-[11px] tabular-nums text-muted-foreground"
      >
        {etTime(headline.published_at)}
      </time>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          {category && headline.category !== 'news' && (
            <span className={cn('rounded border px-1 text-[10px] font-medium uppercase tracking-wide', category.tone)}>
              {category.label}
            </span>
          )}
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
            {headline.publisher ?? SOURCE_LABEL[headline.source]}
          </span>
        </div>
        <p
          className={cn(
            'mt-0.5 text-[13px] leading-snug text-foreground/90',
            headline.category && LOUD.has(headline.category) && 'font-semibold text-foreground',
          )}
        >
          {headline.title}
        </p>
        {headline.symbols.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {headline.symbols.map(symbol => (
              <TickerChip key={symbol} symbol={symbol} quote={quotes[symbol]} onClick={onTicker} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
