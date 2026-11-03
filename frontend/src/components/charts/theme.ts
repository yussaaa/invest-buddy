/**
 * Shared look for the recharts panels.
 *
 * `components/charts/` (plural) is the library layer — thin primitives over
 * recharts. `components/chart/` (singular) is the charting *feature*. The
 * collision is unfortunate; the plural directory already existed, empty, and
 * this is what it was evidently for.
 *
 * Colours are taken from PriceChart rather than restated, because a 200-day
 * average drawn purple on the price chart and blue on the slope chart is a bug,
 * not a style choice.
 */

import { MA_COLORS, MA_FALLBACK_COLOR } from '@/components/chart/PriceChart'

export { MA_COLORS, MA_FALLBACK_COLOR }

export const AXIS = {
  stroke: 'rgba(148,163,184,0.35)',
  tick: { fill: 'rgba(148,163,184,0.9)', fontSize: 10 },
  tickLine: false,
  axisLine: false,
} as const

export const GRID = {
  stroke: 'rgba(148,163,184,0.07)',
  strokeDasharray: '0',
  vertical: false,
} as const

/** Emerald / rose, matching `tone()` in the panels. */
export const UP = '#34d399'
export const DOWN = '#fb7185'
export const NEUTRAL = 'rgba(148,163,184,0.5)'

/** The ±1.5σ envelope. Barely there on purpose — it is context, not a series. */
export const BAND_FILL = 'rgba(148,163,184,0.10)'

export const REFERENCE_LINE = {
  stroke: 'rgba(148,163,184,0.45)',
  strokeDasharray: '3 3',
} as const

/** Ticks are dense on a two-year daily series; show roughly one per quarter. */
export function dateTicks(dates: string[], count = 8): string[] {
  if (dates.length <= count) return dates
  const step = Math.floor(dates.length / count)
  return dates.filter((_, i) => i % step === 0)
}

export function shortDate(iso: string): string {
  const [y, m] = iso.split('-')
  return `${m}/${y.slice(2)}`
}
