/**
 * Pure formatting for the Signals page — tested in format.test.ts.
 *
 * Kept apart from the components so the rules that decide what a reader sees
 * ("is +8 places a climb?", "is z = 1.9 worth a colour?") have one home.
 */

/** Rank change as an arrow and places. Positive means it climbed the board. */
export function rankDelta(change: number | null | undefined): { text: string; tone: 'up' | 'down' | 'flat' | 'new' } {
  if (change === null || change === undefined) return { text: 'new', tone: 'new' }
  if (change === 0) return { text: '—', tone: 'flat' }
  return change > 0
    ? { text: `▲${change}`, tone: 'up' }
    : { text: `▼${Math.abs(change)}`, tone: 'down' }
}

export function signedPct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const sign = value > 0 ? '+' : value < 0 ? '−' : ''
  return `${sign}${Math.abs(value).toFixed(digits)}%`
}

export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1e12) return `${(value / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `${(value / 1e9).toFixed(1)}B`
  if (abs >= 1e6) return `${(value / 1e6).toFixed(1)}M`
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}K`
  return String(Math.round(value))
}

/** The attention column: a z-score once there is history, or how much there is. */
export function attentionLabel(z: number | null | undefined, historyDays: number, minDays = 7): {
  text: string
  emphasis: boolean
  title: string
} {
  if (z !== null && z !== undefined && Number.isFinite(z)) {
    return {
      text: `${z > 0 ? '+' : ''}${z.toFixed(1)}σ`,
      emphasis: z >= 2,
      title: `Mentions vs this ticker's own ${historyDays}-day baseline (robust z-score)`,
    }
  }
  return {
    text: historyDays > 0 ? `day ${Math.min(historyDays, minDays)} of ${minDays}` : '—',
    emphasis: false,
    title: historyDays < minDays
      ? `${historyDays} of ${minDays} days of stored history — compared with yesterday until then`
      : 'History too flat to measure a spread',
  }
}

/**
 * Market-cap tiers. Large/mid/small match the Market page's movers tiers
 * (market_data.CAP_TIERS); mega and micro are the conventional ends.
 * Cool hues for the big, liquid names and warm ones for the small, so size —
 * and with it how violently a name can move — reads at a glance.
 */
export const CAP_TIERS = [
  { id: 'mega', label: 'Mega', range: '$200B+', min: 200e9, className: 'bg-violet-500/15 text-violet-300' },
  { id: 'large', label: 'Large', range: '$10–200B', min: 10e9, className: 'bg-sky-500/15 text-sky-300' },
  { id: 'mid', label: 'Mid', range: '$2–10B', min: 2e9, className: 'bg-teal-500/15 text-teal-300' },
  { id: 'small', label: 'Small', range: '$300M–2B', min: 300e6, className: 'bg-amber-500/15 text-amber-300' },
  { id: 'micro', label: 'Micro', range: 'under $300M', min: 0, className: 'bg-rose-500/15 text-rose-300' },
] as const

export type CapTier = (typeof CAP_TIERS)[number]

export function capTier(marketCap: number | null | undefined): CapTier | null {
  if (marketCap === null || marketCap === undefined || !Number.isFinite(marketCap) || marketCap <= 0) return null
  return CAP_TIERS.find(t => marketCap >= t.min) ?? null
}

/**
 * The trailing one-year price change, as a diverging heat cell: green for
 * up, red for down, and a stronger tint the bigger the move. It describes
 * where the stock has been — a halving can be a setup or a falling knife,
 * and the colour does not say which.
 */
export const YEAR_BANDS = [
  { min: 100, className: 'bg-emerald-500/25 text-emerald-200 font-semibold', label: '+100% or more' },
  { min: 25, className: 'bg-emerald-500/15 text-emerald-300', label: '+25% to +100%' },
  { min: 0, className: 'text-emerald-400/80', label: '0 to +25%' },
  { min: -25, className: 'text-rose-400/80', label: '0 to −25%' },
  { min: -50, className: 'bg-rose-500/15 text-rose-300', label: '−25% to −50%' },
  { min: -Infinity, className: 'bg-rose-500/25 text-rose-200 font-semibold', label: '−50% or worse' },
] as const

export function yearChangeBand(pct: number | null | undefined) {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return null
  return YEAR_BANDS.find(b => pct >= b.min) ?? null
}

/** Relative volume, which is only interesting when it is not ~1. */
export function relVolume(value: number | null | undefined): { text: string; emphasis: boolean } {
  if (value === null || value === undefined || !Number.isFinite(value)) return { text: '—', emphasis: false }
  return { text: `${value.toFixed(1)}×`, emphasis: value >= 2 }
}

/** Share of tagged messages, for the crowd bar. */
export function crowdSplit(bullish: number, bearish: number, untagged: number) {
  const total = bullish + bearish + untagged
  if (total === 0) return { bull: 0, bear: 0, none: 0 }
  return {
    bull: (bullish / total) * 100,
    bear: (bearish / total) * 100,
    none: (untagged / total) * 100,
  }
}

/**
 * Split text on `[S#]` markers so they can render as links to the sources list.
 * Markers that name no known source are dropped (the backend strips them too).
 */
export function splitCitations(text: string, known: Set<string>): (string | { id: string })[] {
  const out: (string | { id: string })[] = []
  let last = 0
  for (const match of text.matchAll(/\[(S\d{1,2})\]/g)) {
    const index = match.index ?? 0
    if (index > last) out.push(text.slice(last, index))
    if (known.has(match[1])) out.push({ id: match[1] })
    last = index + match[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}
