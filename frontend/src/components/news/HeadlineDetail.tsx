/**
 * The selected headline: what was said, by whom, when — and where to go next.
 *
 * Everything here is the source's own words. The interpretation layers
 * (importance, exposure, a cited "why it matters", the measured reaction)
 * attach to this panel as they are built; until then it says nothing it
 * cannot source.
 */

import { Link } from 'react-router-dom'
import { BarChart3, ExternalLink, FlaskConical, X } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type { NewsHeadline, Quote } from '@/lib/types'
import { CATEGORY_META, SOURCE_LABEL, ago, etFull } from './format'

export default function HeadlineDetail({
  headline,
  quotes,
  onClose,
  className,
}: {
  headline: NewsHeadline
  quotes: Record<string, Quote>
  onClose: () => void
  className?: string
}) {
  const category = headline.category ? CATEGORY_META[headline.category] : null
  return (
    <Card className={cn('flex flex-col overflow-hidden', className)}>
      <CardContent className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
            {category && (
              <span className={cn('rounded border px-1 text-[10px] font-medium uppercase tracking-wide', category.tone)}>
                {category.label}
              </span>
            )}
            <span>{headline.publisher ?? SOURCE_LABEL[headline.source]}</span>
            <span className="text-muted-foreground/50">·</span>
            <span title={etFull(headline.published_at)}>{ago(headline.published_at)}</span>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="Close headline"
          >
            <X size={14} />
          </button>
        </div>

        <div>
          <h2 className="text-base font-semibold leading-snug text-foreground">{headline.title}</h2>
          <p className="mt-1 text-[11px] text-muted-foreground/70">{etFull(headline.published_at)}</p>
        </div>

        {headline.summary && <p className="text-[13px] leading-relaxed text-foreground/80">{headline.summary}</p>}

        {headline.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {headline.tags.map(tag => (
              <span key={tag} className="rounded bg-muted px-1.5 py-px text-[10px] text-muted-foreground">
                {tag}
              </span>
            ))}
          </div>
        )}

        {headline.url && (
          <a
            href={headline.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex w-fit items-center gap-1 text-[12px] text-primary hover:underline"
          >
            Read at {headline.publisher ?? SOURCE_LABEL[headline.source]} <ExternalLink size={12} />
          </a>
        )}

        {headline.symbols.length > 0 && (
          <div className="flex flex-col gap-2">
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Tickers named</h3>
            {headline.symbols.map(symbol => {
              const pct = quotes[symbol]?.change_percent
              return (
                <div
                  key={symbol}
                  className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2"
                >
                  <div className="flex items-baseline gap-2">
                    <span className="font-mono text-sm font-semibold">{symbol}</span>
                    {quotes[symbol]?.last !== undefined && (
                      <span className="font-mono text-[12px] tabular-nums text-foreground/80">
                        {quotes[symbol]!.last!.toFixed(2)}
                      </span>
                    )}
                    {pct !== undefined && (
                      <span
                        className={cn(
                          'whitespace-nowrap font-mono text-[11px] tabular-nums',
                          pct > 0 ? 'text-emerald-400' : pct < 0 ? 'text-rose-400' : 'text-muted-foreground',
                        )}
                      >
                        {pct >= 0 ? '+' : ''}
                        {pct.toFixed(2)}% last session
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    <Link
                      to={`/charting?ticker=${encodeURIComponent(symbol)}`}
                      className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <BarChart3 size={12} /> Chart
                    </Link>
                    <Link
                      to={`/analyze?ticker=${encodeURIComponent(symbol)}`}
                      className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] text-primary hover:bg-primary/10"
                    >
                      <FlaskConical size={12} /> Analyze
                    </Link>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
