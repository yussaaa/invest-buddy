/**
 * The deterministic half of an answer, rendered before the prose arrives.
 *
 * These come from the `context` SSE event, which the backend emits off cached
 * indicators before the model has produced a token. Showing them first is the
 * point: the grounded part of the reply is on screen in a couple of hundred
 * milliseconds while the narration streams in underneath.
 *
 * Colours match TechnicalPanel's emerald/rose/muted convention so the chips
 * read as the same system as the indicator cards on the page behind them.
 */

import { useState } from 'react'
import { ChevronDown, TriangleAlert } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { NetBias, Signal, SignalSet } from '@/lib/types'

const DIRECTION_STYLE: Record<string, string> = {
  bullish: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400',
  bearish: 'border-rose-500/30 bg-rose-500/10 text-rose-400',
  neutral: 'border-border bg-muted/40 text-muted-foreground',
}

const BIAS_LABEL: Record<NetBias, string> = {
  bullish: 'Leaning bullish',
  bearish: 'Leaning bearish',
  mixed: 'Mixed',
  inconclusive: 'Inconclusive',
}

const BIAS_STYLE: Record<NetBias, string> = {
  bullish: 'text-emerald-400',
  bearish: 'text-rose-400',
  mixed: 'text-amber-400',
  inconclusive: 'text-muted-foreground',
}

/** Horizon matters more than most readers expect, so it is always on the chip. */
const TIMEFRAME_LABEL: Record<string, string> = {
  intraday: 'intraday',
  short: 'days',
  medium: 'weeks',
  long: 'months',
}

function SignalRow({ signal }: { signal: Signal }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded-lg border border-border/60 bg-card/40">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left"
        aria-expanded={open}
      >
        <span
          className={cn(
            'shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium',
            DIRECTION_STYLE[signal.direction] ?? DIRECTION_STYLE.neutral,
          )}
        >
          {signal.label}
        </span>
        <span className="text-[10px] text-muted-foreground/60">
          {TIMEFRAME_LABEL[signal.timeframe] ?? signal.timeframe}
        </span>
        {signal.reliability === 'low' && (
          <span className="text-[10px] text-muted-foreground/50">· weak</span>
        )}
        <ChevronDown
          size={13}
          className={cn(
            'ml-auto shrink-0 text-muted-foreground/50 transition-transform',
            open && 'rotate-180',
          )}
        />
      </button>

      {open && (
        <div className="space-y-1.5 px-2.5 pb-2 text-[11px] leading-relaxed text-muted-foreground">
          <p>{signal.rationale}</p>
          {signal.invalidation && (
            // The field that turns a call into a condition — worth its own line.
            <p className="text-muted-foreground/70">
              <span className="text-muted-foreground/50">Invalidated: </span>
              {signal.invalidation}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

export default function SignalChips({ signalSet }: { signalSet: SignalSet | null }) {
  if (!signalSet) return null

  const { signals, net_bias, conflicts } = signalSet

  if (!signals.length) {
    return (
      <div className="border-b border-border px-3 py-2 text-[11px] text-muted-foreground/60">
        No technical condition on {signalSet.ticker} stands out right now — the
        readings are in their ordinary ranges.
      </div>
    )
  }

  return (
    <div className="space-y-1.5 border-b border-border px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
          {signalSet.ticker}
        </span>
        <span className={cn('text-[11px] font-medium', BIAS_STYLE[net_bias])}>
          {BIAS_LABEL[net_bias]}
        </span>
        <span className="ml-auto text-[10px] tabular-nums text-muted-foreground/50">
          {signals.length} condition{signals.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="space-y-1">
        {signals.map(signal => (
          <SignalRow key={signal.id} signal={signal} />
        ))}
      </div>

      {conflicts.length > 0 && (
        // Surfaced rather than buried: it is what stops the answer being read
        // as one-directional when the horizons disagree.
        <div className="flex gap-1.5 rounded-lg bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-400/90">
          <TriangleAlert size={12} className="mt-0.5 shrink-0" />
          <span>{conflicts.join('; ')}.</span>
        </div>
      )}
    </div>
  )
}
