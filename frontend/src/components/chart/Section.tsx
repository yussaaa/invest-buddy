/**
 * A collapsible panel on the charting page.
 *
 * Deliberately not built on ui/collapsible.tsx. That component animates its
 * height, which means it must render its children into the DOM to measure
 * `scrollHeight` — so a closed panel would still mount its charts and still
 * pull the recharts chunk. The whole reason these sections exist is that the
 * page had grown past five screens; mounting everything anyway would fix the
 * scrolling and none of the cost.
 *
 * So: mount on first open, `hidden` thereafter. Hiding rather than unmounting
 * on the way back means collapsing a panel does not throw away a loaded chart
 * or a model explanation that was paid for. The trade is the height animation,
 * which cannot coexist with not rendering the content.
 *
 * Reopening is cheap for a reason that lives elsewhere: every panel fetches
 * through the client cache, so a remount inside the TTL is a synchronous read
 * with no request and no spinner.
 */

import { useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { SectionId } from './sectionPrefs'

interface SectionProps {
  id: SectionId
  title: string
  /** The methodology line, right-aligned in the header. */
  sublabel?: string
  /** A one-line digest shown when closed, so collapsing hides detail rather
   *  than hiding that the panel has anything to say. */
  summary?: ReactNode
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
}

export default function Section({
  id,
  title,
  sublabel,
  summary,
  open,
  onOpenChange,
  children,
}: SectionProps) {
  // Latch: once opened, the content stays mounted for the rest of the session.
  const [mounted, setMounted] = useState(open)
  if (open && !mounted) setMounted(true)

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <button
          type="button"
          onClick={() => onOpenChange(!open)}
          aria-expanded={open}
          aria-controls={`section-${id}`}
          className="group flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground transition-colors hover:text-foreground"
        >
          <ChevronDown
            size={14}
            className={cn(
              'shrink-0 transition-transform duration-200',
              open ? 'rotate-0' : '-rotate-90'
            )}
          />
          {title}
        </button>

        {open ? (
          sublabel && <span className="text-[11px] text-muted-foreground/60">{sublabel}</span>
        ) : (
          <span className="text-[11px] tabular-nums text-muted-foreground/60">{summary}</span>
        )}
      </div>

      {mounted && (
        <div id={`section-${id}`} hidden={!open}>
          {children}
        </div>
      )}
    </section>
  )
}
