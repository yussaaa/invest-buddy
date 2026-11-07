/**
 * Display helpers for the news wire. Times are New York time — the market's
 * clock — never the viewer's, and never UTC (see CLAUDE.md: after 8pm ET the
 * UTC date has already rolled over).
 */

import type { NewsCategory, NewsSourceId } from '@/lib/types'

const MARKET_TZ = 'America/New_York'

const timeFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: MARKET_TZ, hour: '2-digit', minute: '2-digit', hour12: false,
})
const dayFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: MARKET_TZ, weekday: 'short', month: 'short', day: 'numeric',
})
const keyFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: MARKET_TZ, year: 'numeric', month: '2-digit', day: '2-digit',
})
const fullFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: MARKET_TZ, weekday: 'short', month: 'short', day: 'numeric',
  hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
})

/** "13:31" in New York. */
export function etTime(iso: string): string {
  return timeFmt.format(new Date(iso))
}

/** "2026-11-06" — the New York calendar date, for grouping rows by day. */
export function etDayKey(iso: string): string {
  return keyFmt.format(new Date(iso))
}

/** "Today", "Yesterday", or "Wed, Nov 4" — relative to `now`, in New York. */
export function etDayLabel(iso: string, now: Date = new Date()): string {
  const key = etDayKey(iso)
  if (key === keyFmt.format(now)) return 'Today'
  if (key === keyFmt.format(new Date(now.getTime() - 86_400_000))) return 'Yesterday'
  return dayFmt.format(new Date(iso))
}

/** "Fri, Nov 6, 8:20 AM EST". */
export function etFull(iso: string): string {
  return fullFmt.format(new Date(iso))
}

/** "just now", "4m ago", "3h ago", "2d ago". */
export function ago(iso: string, now: Date = new Date()): string {
  const seconds = Math.max(0, (now.getTime() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86_400)}d ago`
}

export const CATEGORY_META: Record<NewsCategory, { label: string; tone: string }> = {
  monetary_policy: { label: 'Monetary policy', tone: 'border-sky-500/40 text-sky-400' },
  earnings: { label: 'Earnings', tone: 'border-violet-500/40 text-violet-400' },
  deal: { label: 'Deal', tone: 'border-amber-500/40 text-amber-400' },
  management: { label: 'Management', tone: 'border-teal-500/40 text-teal-400' },
  accounting: { label: 'Accounting', tone: 'border-rose-500/40 text-rose-400' },
  corporate: { label: 'Corporate', tone: 'border-orange-500/40 text-orange-400' },
  regulatory: { label: 'Regulatory', tone: 'border-slate-400/40 text-slate-400' },
  news: { label: 'News', tone: 'border-border text-muted-foreground' },
}

export const SOURCE_LABEL: Record<NewsSourceId, string> = {
  alpaca: 'Newswire',
  edgar: 'SEC 8-K',
  fed: 'Fed',
}
