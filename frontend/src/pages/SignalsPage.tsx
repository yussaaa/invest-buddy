/**
 * Signals — what the crowd is talking about, and what the screens are finding.
 *
 * Two lists on the left (the Reddit mention board, Yahoo's preset screens),
 * one ticker on the right. Clicking a row, or a watchlist entry, opens that
 * ticker's crowd read: attention, self-tagged sentiment, short interest, the
 * deterministic conditions derived from them, and — on request — a guarded
 * narration and a cited research brief.
 *
 * Everything shown in a table is a reading; every condition is computed in
 * Python (services/social_signals.py). The language model only appears
 * behind a button, and only ever narrates what is already on screen.
 */

import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertCircle, Radar } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { useScreenContext } from '@/context/ScreenContext'
import { useCachedResource } from '@/hooks/useCachedResource'
import { api } from '@/lib/api'
import { K, TTL } from '@/lib/cacheKeys'
import { readJSON, writeJSON } from '@/lib/utils'
import SegmentedToggle, { type SegmentOption } from '@/components/signals/SegmentedToggle'
import SourceStatusStrip from '@/components/signals/SourceStatusStrip'
import SocialTrendingTable from '@/components/signals/SocialTrendingTable'
import ScreenerTable from '@/components/signals/ScreenerTable'
import TickerSignalPanel from '@/components/signals/TickerSignalPanel'

const PREFS_KEY = 'agent-invest-signals'
const TRENDING_LIMIT = 30
const SCREEN_LIMIT = 25

interface Prefs {
  source: string
  filter: string
  preset: string
}

const DEFAULT_PREFS: Prefs = { source: 'apewisdom', filter: 'all-stocks', preset: 'most_actives' }

function Section({
  title,
  hint,
  controls,
  children,
}: {
  title: string
  hint?: string
  controls?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
          {hint && <p className="mt-0.5 text-[11px] text-muted-foreground/60">{hint}</p>}
        </div>
        {controls}
      </div>
      {children}
    </div>
  )
}

export default function SignalsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const selected = searchParams.get('ticker')?.toUpperCase() ?? null
  const [prefs, setPrefs] = useState<Prefs>(() => ({ ...DEFAULT_PREFS, ...readJSON(PREFS_KEY, DEFAULT_PREFS) }))

  useEffect(() => writeJSON(PREFS_KEY, prefs), [prefs])

  // Tell the chat agent which ticker is open. `publish` merges and never
  // clears, so Charting's range/chart_type may linger — harmless, since the
  // agent's context step reads only the ticker.
  const { publish } = useScreenContext()
  useEffect(() => {
    publish({ route: '/signals', ticker: selected ?? undefined })
  }, [publish, selected])

  const select = (symbol: string | null) =>
    setSearchParams(symbol ? { ticker: symbol } : {}, { replace: true })

  const sourcesRes = useCachedResource(K.signalSources(), () => api.signals.sources(), {
    ttl: TTL.signalSources,
    refreshIntervalMs: TTL.signalSources,
  })
  const sources = sourcesRes.data?.sources ?? []
  const filters = sourcesRes.data?.filters ?? [{ id: 'all-stocks', label: 'All stocks' }]

  const trendingRes = useCachedResource(
    K.socialTrending(prefs.source, prefs.filter, TRENDING_LIMIT),
    () => api.signals.trending(prefs.source, prefs.filter, TRENDING_LIMIT),
    { ttl: TTL.socialTrending, refreshIntervalMs: TTL.socialTrending, shouldCache: p => p.rows.length > 0 },
  )
  const trending = trendingRes.data

  const presetsRes = useCachedResource(K.screenPresets(), () => api.signals.screeners(), {
    ttl: TTL.screenPresets,
  })
  const presets = presetsRes.data?.presets ?? []

  const screenRes = useCachedResource(
    K.screen(prefs.preset, SCREEN_LIMIT),
    () => api.signals.screen(prefs.preset, SCREEN_LIMIT),
    { ttl: TTL.screen, refreshIntervalMs: TTL.screen, shouldCache: p => p.rows.length > 0 },
  )
  const screen = screenRes.data

  const sourceOptions: SegmentOption<string>[] = useMemo(
    () =>
      sources
        .filter(s => s.capabilities.includes('trending'))
        .map(s => ({
          value: s.id,
          label: s.label,
          disabled: !s.enabled,
          title: s.reason ?? undefined,
        })),
    [sources],
  )

  const activePreset = presets.find(p => p.id === prefs.preset)
  const trendingNote = trending?.caveat ?? trending?.error ?? null

  return (
    <div className="mx-auto flex max-w-[1500px] flex-col gap-6 px-6 py-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
            <Radar size={20} className="text-primary" /> Signals
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            What retail traders are discussing, what the screens are finding, and why a ticker is in focus.
          </p>
        </div>
        <SourceStatusStrip sources={sources} />
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="flex min-w-0 flex-col gap-6">
          <Section
            title="Social trending"
            hint="Most-mentioned tickers on Reddit over 24 hours · attention, not direction"
            controls={
              <div className="flex flex-wrap items-center gap-2">
                {sourceOptions.length > 1 && (
                  <SegmentedToggle
                    options={sourceOptions}
                    value={prefs.source}
                    onChange={source => setPrefs(p => ({ ...p, source }))}
                  />
                )}
                {prefs.source === 'apewisdom' && (
                  <SegmentedToggle
                    options={filters.map(f => ({ value: f.id, label: f.label }))}
                    value={prefs.filter}
                    onChange={filter => setPrefs(p => ({ ...p, filter }))}
                  />
                )}
              </div>
            }
          >
            {trendingNote && (
              <Alert variant={trending?.available === false ? 'destructive' : 'default'}>
                <AlertCircle size={16} />
                <AlertDescription>{trendingNote}</AlertDescription>
              </Alert>
            )}
            <Card>
              <CardContent className="py-2">
                <SocialTrendingTable
                  rows={trending?.rows ?? []}
                  loading={trendingRes.isLoading}
                  selected={selected}
                  onSelect={select}
                />
              </CardContent>
            </Card>
          </Section>

          <Section
            title="Screeners"
            hint={activePreset?.hint ?? "Yahoo's predefined screens"}
            controls={
              presets.length > 0 && (
                <SegmentedToggle
                  options={presets.map(p => ({ value: p.id, label: p.label, title: p.hint }))}
                  value={prefs.preset}
                  onChange={preset => setPrefs(p => ({ ...p, preset }))}
                />
              )
            }
          >
            {screen?.error && (
              <Alert variant="destructive">
                <AlertCircle size={16} />
                <AlertDescription>{screen.error}</AlertDescription>
              </Alert>
            )}
            <Card>
              <CardContent className="py-2">
                <ScreenerTable
                  rows={screen?.rows ?? []}
                  loading={screenRes.isLoading}
                  selected={selected}
                  onSelect={select}
                />
              </CardContent>
            </Card>
          </Section>

          {sourcesRes.data?.disclaimer && (
            <p className="text-[11px] leading-snug text-muted-foreground/50">{sourcesRes.data.disclaimer}</p>
          )}
        </div>

        <div className="xl:sticky xl:top-6">
          {selected ? (
            <TickerSignalPanel key={selected} symbol={selected} onClose={() => select(null)} />
          ) : (
            <Card>
              <CardContent className="py-10 text-center text-[12px] text-muted-foreground/70">
                Select a row — or a watchlist ticker — to see its crowd read, its chart conditions,
                and a cited brief on why it is in focus.
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
