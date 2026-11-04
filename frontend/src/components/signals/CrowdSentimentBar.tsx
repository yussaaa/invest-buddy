/**
 * StockTwits self-tags as one stacked bar: bullish, bearish, untagged.
 *
 * Untagged messages are drawn rather than dropped. "80% bullish" over 10
 * tagged of 60 is a different reading from 80% of 60, and the grey segment
 * is how the bar says so without a footnote.
 */

import type { CrowdTags } from '@/lib/types'
import { crowdSplit } from './format'

export default function CrowdSentimentBar({ crowd }: { crowd: CrowdTags | null }) {
  if (!crowd || !crowd.available) {
    return (
      <p className="text-[11px] leading-relaxed text-muted-foreground/70">
        {crowd?.caveat ?? 'StockTwits data is not available.'}
      </p>
    )
  }
  const split = crowdSplit(crowd.bullish, crowd.bearish, crowd.untagged)
  const tagged = crowd.bullish + crowd.bearish

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between text-[11px]">
        <span className="text-emerald-400">
          {crowd.bull_pct !== null ? `${crowd.bull_pct.toFixed(0)}% bullish` : 'No tags'}
        </span>
        <span className="text-muted-foreground/60">
          {tagged} tagged of {crowd.sampled}
          {crowd.window_hours !== null && ` · last ${crowd.window_hours < 1 ? '<1' : crowd.window_hours.toFixed(0)}h`}
        </span>
      </div>
      <div className="flex h-2 overflow-hidden rounded-full bg-muted" role="img"
           aria-label={`${crowd.bullish} bullish, ${crowd.bearish} bearish, ${crowd.untagged} untagged`}>
        <div className="bg-emerald-400/80" style={{ width: `${split.bull}%` }} />
        <div className="bg-rose-400/80" style={{ width: `${split.bear}%` }} />
        <div className="bg-muted-foreground/20" style={{ width: `${split.none}%` }} />
      </div>
      <p className="text-[10px] leading-snug text-muted-foreground/50">
        Self-tagged and skewed bullish by habit; ~60–65% bullish is the resting level.
      </p>
    </div>
  )
}
