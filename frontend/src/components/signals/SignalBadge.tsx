/**
 * Compact chips for social signal ids in table rows.
 *
 * Attention signals are neutral by construction (services/social_signals.py),
 * so they get an informational tone rather than green or red — a mention
 * spike is not good news or bad news, only news.
 */

import { cn } from '@/lib/utils'

const BADGES: Record<string, { label: string; title: string; tone: 'info' | 'bull' | 'bear' | 'muted' }> = {
  mention_spike: { label: 'Spike', title: 'Mentions ≥2σ above this ticker’s own baseline', tone: 'info' },
  mention_surge_24h: { label: 'Surge', title: 'Mentions ≥3× yesterday (no baseline yet)', tone: 'info' },
  rank_climb: { label: 'Climbing', title: 'Up 15+ places, or new to the top 20', tone: 'info' },
  attention_fading: { label: 'Fading', title: 'Mentions halved from a top-20 rank', tone: 'muted' },
  crowd_bullish: { label: 'Crowd bullish', title: '≥80% of tagged messages bullish', tone: 'bull' },
  crowd_bearish: { label: 'Crowd bearish', title: '≤45% of tagged messages bullish', tone: 'bear' },
  crowd_one_sided: { label: 'One-sided', title: '≥90% agree while attention is elevated', tone: 'info' },
  short_interest_high: { label: 'High short', title: '≥20% of float sold short', tone: 'info' },
}

const TONE: Record<string, string> = {
  info: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
  bull: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400',
  bear: 'border-rose-500/30 bg-rose-500/10 text-rose-400',
  muted: 'border-border bg-muted/40 text-muted-foreground',
}

export default function SignalBadge({ id }: { id: string }) {
  const badge = BADGES[id] ?? { label: id, title: id, tone: 'muted' as const }
  return (
    <span
      title={badge.title}
      className={cn('shrink-0 rounded border px-1.5 py-0 text-[10px] font-medium leading-4', TONE[badge.tone])}
    >
      {badge.label}
    </span>
  )
}
