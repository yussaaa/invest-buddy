/**
 * News — a live wire of market-moving headlines.
 *
 * Three sources, each honest about its state in the strip at the top:
 * Alpaca's newsroom feed (Benzinga, ticker-tagged, pushed over a websocket),
 * SEC 8-K filings that matter, and Federal Reserve releases. The backend's
 * runner ingests them; this page reads the stored wire and a stream of what
 * changed, so a headline appears here seconds after it is published.
 *
 * Left: the wire, newest first, grouped by New York trading day. Right: the
 * selected headline in full, its tickers, and the way into the chart or the
 * six-agent analysis. Nothing on this page is generated — the interpretation
 * layers (importance, exposure, a cited explanation, the measured reaction)
 * attach to the same rows as they are built.
 */

import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertCircle, Newspaper, Search, X } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { useScreenContext } from '@/context/ScreenContext'
import { useCachedResource } from '@/hooks/useCachedResource'
import { useNewsWire, type WireStatus } from '@/hooks/useNewsWire'
import { useQuotes } from '@/hooks/useQuotes'
import { api } from '@/lib/api'
import { K, TTL } from '@/lib/cacheKeys'
import { filterHeadlines } from '@/lib/newsReducer'
import type { NewsCategory, NewsHeadline, NewsSourceId } from '@/lib/types'
import { cn, readJSON, writeJSON } from '@/lib/utils'
import SourceStatusStrip from '@/components/signals/SourceStatusStrip'
import HeadlineDetail from '@/components/news/HeadlineDetail'
import HeadlineRow from '@/components/news/HeadlineRow'
import { CATEGORY_META, SOURCE_LABEL, etDayKey, etDayLabel } from '@/components/news/format'

const PREFS_KEY = 'agent-invest-news'
const USER_ID = 'anonymous'
// Quotes for the tickers near the top of the wire — enough to colour what is
// on screen without asking for hundreds of symbols.
const QUOTED_ROWS = 80
const MAX_QUOTED = 60
const FRESH_MS = 3_000

interface Prefs {
  sources: NewsSourceId[]
  categories: NewsCategory[]
  watchlistOnly: boolean
}

const DEFAULT_PREFS: Prefs = { sources: [], categories: [], watchlistOnly: false }
const SOURCE_IDS: NewsSourceId[] = ['alpaca', 'edgar', 'fed']

function toggle<T>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter(x => x !== item) : [...list, item]
}

function Chip({
  active,
  onClick,
  children,
  title,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
  title?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-pressed={active}
      className={cn(
        'h-7 rounded-md border px-2.5 text-xs font-medium transition-colors',
        active
          ? 'border-primary/50 bg-primary/15 text-primary'
          : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground',
      )}
    >
      {children}
    </button>
  )
}

const STATUS_STYLE: Record<WireStatus, { dot: string; label: string }> = {
  live: { dot: 'bg-emerald-400 animate-pulse', label: 'Live' },
  connecting: { dot: 'bg-muted-foreground/50', label: 'Connecting' },
  reconnecting: { dot: 'bg-amber-400', label: 'Reconnecting' },
}

export default function NewsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedId = searchParams.get('headline')
  const tickerFilter = searchParams.get('ticker')?.toUpperCase() ?? null
  const [prefs, setPrefs] = useState<Prefs>(() => ({ ...DEFAULT_PREFS, ...readJSON(PREFS_KEY, DEFAULT_PREFS) }))
  const [query, setQuery] = useState('')
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => writeJSON(PREFS_KEY, prefs), [prefs])
  // Re-render each second only so "fresh" flashes expire; cheap, and stops
  // once nothing is fresh.
  const wire = useNewsWire()
  const anyFresh = [...wire.arrivals.values()].some(t => now - t < FRESH_MS)
  useEffect(() => {
    if (!anyFresh && wire.arrivals.size === 0) return
    const id = setInterval(() => setNow(Date.now()), 1_000)
    return () => clearInterval(id)
  }, [anyFresh, wire.arrivals.size])

  const sourcesRes = useCachedResource(K.newsSources(), () => api.news.sources(), {
    ttl: TTL.newsSources,
    refreshIntervalMs: TTL.newsSources,
  })
  const sources = sourcesRes.data?.sources ?? []
  const categories = sourcesRes.data?.categories ?? []

  const watchlistsRes = useCachedResource(`watchlists:${USER_ID}`, () => api.watchlist.getAll(USER_ID), {
    ttl: 60_000,
    enabled: prefs.watchlistOnly,
  })
  const watchlistSymbols = useMemo(
    () => [...new Set((watchlistsRes.data ?? []).flatMap(w => w.tickers.map(t => t.toUpperCase())))],
    [watchlistsRes.data],
  )

  const visible = useMemo(() => {
    const symbols = tickerFilter ? [tickerFilter] : prefs.watchlistOnly ? watchlistSymbols : null
    return filterHeadlines(wire.headlines, {
      sources: prefs.sources,
      categories: prefs.categories,
      symbols,
      query,
    })
  }, [wire.headlines, prefs, watchlistSymbols, tickerFilter, query])

  const quotedSymbols = useMemo(
    () => [...new Set(visible.slice(0, QUOTED_ROWS).flatMap(h => h.symbols))].slice(0, MAX_QUOTED),
    [visible],
  )
  const { quotes } = useQuotes(quotedSymbols, 30_000)

  const selected: NewsHeadline | null = useMemo(
    () => wire.headlines.find(h => h.id === selectedId) ?? null,
    [wire.headlines, selectedId],
  )

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next, { replace: true })
  }

  // Tell the chat agent which ticker the open headline is about.
  const { publish } = useScreenContext()
  useEffect(() => {
    publish({ route: '/news', ticker: selected?.symbols[0] ?? tickerFilter ?? undefined })
  }, [publish, selected, tickerFilter])

  // Group consecutive rows by New York trading day.
  const groups = useMemo(() => {
    const out: { key: string; label: string; rows: NewsHeadline[] }[] = []
    for (const h of visible) {
      const key = etDayKey(h.published_at)
      let last = out[out.length - 1]
      if (last?.key !== key) {
        last = { key, label: etDayLabel(h.published_at), rows: [] }
        out.push(last)
      }
      last.rows.push(h)
    }
    return out
  }, [visible])

  const status = STATUS_STYLE[wire.status]
  const unconfigured = sources.filter(s => s.state === 'not_configured')
  const filtersActive =
    prefs.sources.length > 0 || prefs.categories.length > 0 || prefs.watchlistOnly || !!tickerFilter || !!query

  return (
    <div className="mx-auto flex max-w-[1500px] flex-col gap-5 px-6 py-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
            <Newspaper size={20} className="text-primary" /> News
            <span
              className="ml-1 inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-[11px] font-normal text-muted-foreground"
              title="Headlines arrive here as the sources publish them"
            >
              <span className={cn('h-1.5 w-1.5 rounded-full', status.dot)} />
              {status.label}
            </span>
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Market-moving headlines as they break — newswires, SEC filings and the Fed.
          </p>
        </div>
        <SourceStatusStrip sources={sources} />
      </div>

      {/* Each group wraps as a unit, so a narrow window never strands a chip. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex flex-wrap items-center gap-1.5" aria-label="Sources">
          {SOURCE_IDS.map(id => (
            <Chip
              key={id}
              active={prefs.sources.includes(id)}
              onClick={() => setPrefs(p => ({ ...p, sources: toggle(p.sources, id) }))}
            >
              {SOURCE_LABEL[id]}
            </Chip>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5" aria-label="Categories">
          {categories
            .filter(c => c !== 'news')
            .map(c => (
              <Chip
                key={c}
                active={prefs.categories.includes(c)}
                onClick={() => setPrefs(p => ({ ...p, categories: toggle(p.categories, c) }))}
              >
                {CATEGORY_META[c]?.label ?? c}
              </Chip>
            ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Chip
            active={prefs.watchlistOnly}
            onClick={() => setPrefs(p => ({ ...p, watchlistOnly: !p.watchlistOnly }))}
            title="Only headlines naming a ticker on one of your watchlists"
          >
            My watchlist
          </Chip>
          {tickerFilter && (
            <button
              onClick={() => setParam('ticker', null)}
              className="inline-flex h-7 items-center gap-1 rounded-md border border-primary/50 bg-primary/15 px-2.5 font-mono text-xs text-primary"
              title="Clear the ticker filter"
            >
              {tickerFilter} <X size={12} />
            </button>
          )}
        </div>
        <div className="relative ml-auto">
          <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Filter headlines"
            className="h-7 w-48 rounded-md border border-border bg-transparent pl-7 pr-2 text-xs outline-none placeholder:text-muted-foreground/60 focus:border-primary/50"
          />
        </div>
      </div>

      {wire.error && (
        <Alert variant="destructive">
          <AlertCircle size={16} />
          <AlertDescription>{wire.error}</AlertDescription>
        </Alert>
      )}

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
        <Card className="overflow-hidden">
          <CardContent className="p-0">
            {wire.isLoading ? (
              <p className="px-4 py-10 text-center text-[12px] text-muted-foreground">Loading the wire…</p>
            ) : visible.length === 0 ? (
              <div className="px-4 py-10 text-center text-[12px] text-muted-foreground">
                {filtersActive ? (
                  'No headlines match these filters.'
                ) : (
                  <>
                    Nothing on the wire yet.
                    {unconfigured.length > 0 && (
                      <span className="mt-2 block text-muted-foreground/70">
                        {unconfigured.map(s => s.reason).join(' ')}
                      </span>
                    )}
                  </>
                )}
              </div>
            ) : (
              groups.map(group => (
                <section key={group.key}>
                  <h2 className="sticky top-0 z-10 border-b border-border bg-card/95 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground backdrop-blur">
                    {group.label}
                    <span className="ml-2 font-normal text-muted-foreground/50">{group.rows.length}</span>
                  </h2>
                  {group.rows.map(h => (
                    <HeadlineRow
                      key={h.id}
                      headline={h}
                      quotes={quotes}
                      selected={h.id === selectedId}
                      fresh={now - (wire.arrivals.get(h.id) ?? 0) < FRESH_MS}
                      onSelect={id => setParam('headline', id === selectedId ? null : id)}
                      onTicker={symbol => setParam('ticker', symbol)}
                    />
                  ))}
                </section>
              ))
            )}
          </CardContent>
        </Card>

        <div className="xl:sticky xl:top-6">
          {selected ? (
            <HeadlineDetail
              key={selected.id}
              headline={selected}
              quotes={quotes}
              onClose={() => setParam('headline', null)}
              className="xl:max-h-[calc(100vh-3rem)]"
            />
          ) : (
            <Card>
              <CardContent className="py-10 text-center text-[12px] text-muted-foreground/70">
                Select a headline to read it in full and jump to its tickers. Click a ticker on the wire to see only
                its headlines.
              </CardContent>
            </Card>
          )}
          {sourcesRes.data?.disclaimer && (
            <p className="mt-3 text-[11px] leading-snug text-muted-foreground/50">{sourcesRes.data.disclaimer}</p>
          )}
        </div>
      </div>
    </div>
  )
}
