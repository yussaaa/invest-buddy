/**
 * Turn the API's column-oriented series into the rows recharts wants.
 *
 * The backend ships columns because a row per session would repeat every key
 * name five hundred times. recharts wants rows. This is where that conversion
 * happens — once per payload, in a module rather than inline, because it has
 * real edge cases: a band needs both endpoints to draw, any series can be null
 * during its warm-up, and a backend regression could hand back arrays of
 * differing lengths.
 */

import type { TrendSeries } from '@/lib/types'

export interface TrendRow {
  date: string
  price: number | null
  sma20: number | null
  sma50: number | null
  sma200: number | null
  /** [lower, upper] — recharts draws a range Area from a two-element array. */
  band: [number, number] | null
  slope20: number | null
  slope50: number | null
  slope200: number | null
  z: number | null
}

function at(values: (number | null)[] | undefined, i: number): number | null {
  return values?.[i] ?? null
}

export function zipTrend(series: TrendSeries | undefined): TrendRow[] {
  if (!series?.dates?.length) return []

  // Never read past the shortest column: a mismatch is a backend bug, and
  // rendering undefined as a data point is a worse way to find out about it.
  const lengths = [
    series.dates.length,
    series.price?.length ?? 0,
    series.z_score?.length ?? 0,
  ]
  const n = Math.min(...lengths)

  const rows: TrendRow[] = []
  for (let i = 0; i < n; i++) {
    const lower = at(series.band_lower, i)
    const upper = at(series.band_upper, i)
    rows.push({
      date: series.dates[i],
      price: at(series.price, i),
      sma20: at(series.sma?.['20'], i),
      sma50: at(series.sma?.['50'], i),
      sma200: at(series.sma?.['200'], i),
      // Both endpoints or nothing — half a band is not a band.
      band: lower != null && upper != null ? [lower, upper] : null,
      slope20: at(series.slope_per_day?.['20'], i),
      slope50: at(series.slope_per_day?.['50'], i),
      slope200: at(series.slope_per_day?.['200'], i),
      z: at(series.z_score, i),
    })
  }
  return rows
}
