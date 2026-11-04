/**
 * Which social sources are live, refused, or not set up — said up front.
 *
 * An empty crowd panel reads as "nobody is talking about this" unless the
 * page has already said StockTwits is blocked. This is where it says so.
 */

import { cn } from '@/lib/utils'
import type { SocialSourceStatus } from '@/lib/types'

const STATE_STYLE: Record<string, { dot: string; label: string }> = {
  live: { dot: 'bg-emerald-400', label: 'live' },
  blocked: { dot: 'bg-amber-400', label: 'blocked' },
  disabled: { dot: 'bg-muted-foreground/40', label: 'off' },
  not_configured: { dot: 'bg-muted-foreground/40', label: 'not set up' },
}

export default function SourceStatusStrip({ sources }: { sources: SocialSourceStatus[] }) {
  if (!sources.length) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {sources.map(source => {
        const style = STATE_STYLE[source.state] ?? STATE_STYLE.disabled
        return (
          <span
            key={source.id}
            title={source.reason ?? `${source.label} is ${style.label}`}
            className={cn(
              'flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-[11px]',
              source.state === 'live' ? 'text-foreground/80' : 'text-muted-foreground/70',
            )}
          >
            <span className={cn('h-1.5 w-1.5 rounded-full', style.dot)} />
            {source.label}
            <span className="text-muted-foreground/50">· {style.label}</span>
          </span>
        )
      })}
    </div>
  )
}
