/**
 * Remembering where the user had zoomed and panned a chart.
 *
 * The chart canvas is rebuilt on every mount (and on every chart-type change),
 * so without this the view snaps back to "fit everything" each time you come
 * back to a ticker, even though the candles themselves are instant from the
 * client cache. The view is kept per symbol and range, in memory only: a page
 * reload should refit.
 *
 * The range is stored anchored to the *latest* bar, not as raw logical
 * indexes. History refreshes every 30s and a new session appends a bar; a raw
 * index range would then stop one bar short of the right edge. Anchored, the
 * window slides with the data, which is what a trader expects. A view that
 * showed everything is remembered as just that, so it keeps fitting as the
 * series grows.
 */

import { peek, put } from '@/lib/clientCache'

export interface LogicalRange {
  from: number
  to: number
}

export interface SavedView {
  /** Bars between the window's left edge and the latest bar. */
  fromEnd: number
  /** Bars between the window's right edge and the latest bar (negative = right margin). */
  toEnd: number
  /** The window covered the whole series — restore by fitting, not by indexes. */
  fit: boolean
}

const key = (viewKey: string) => `chartview:${viewKey}`

/** Half a bar of slack: lightweight-charts reports fractional edges. */
const EDGE = 0.5

export function toSaved(range: LogicalRange, barCount: number): SavedView {
  const last = barCount - 1
  return {
    fromEnd: last - range.from,
    toEnd: last - range.to,
    fit: range.from <= EDGE && range.to >= last - EDGE,
  }
}

/** The range to show for `barCount` bars, or null to fit the content. */
export function toRange(saved: SavedView | undefined, barCount: number): LogicalRange | null {
  if (!saved || saved.fit || barCount < 2) return null
  const last = barCount - 1
  const range = { from: last - saved.fromEnd, to: last - saved.toEnd }
  // The window no longer overlaps the data (a shorter series than when it was
  // saved): there is nothing sensible to restore.
  if (range.to < 0 || range.from > last || range.to <= range.from) return null
  return range
}

export function rememberView(viewKey: string, range: LogicalRange, barCount: number): void {
  if (barCount < 2) return
  put(key(viewKey), toSaved(range, barCount))
}

export function recallView(viewKey: string, barCount: number): LogicalRange | null {
  return toRange(peek<SavedView>(key(viewKey)).value, barCount)
}
