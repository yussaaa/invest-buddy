/**
 * Everything the Signals page knows about one ticker, in one column.
 *
 * Order is deliberate: attention and crowd (what people are doing) before
 * the chart's conditions (what price is doing), and the language model last,
 * behind buttons — the numbers above it are deterministic and load first.
 */

import { lazy, Suspense, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CandlestickChart, ExternalLink, Loader2, Search, ShieldAlert, Sparkles, X } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import SignalChips from '@/components/chat/SignalChips'
import { api } from '@/lib/api'
import { K, TTL } from '@/lib/cacheKeys'
import { useCachedResource } from '@/hooks/useCachedResource'
import { useResearchStream } from '@/hooks/useResearchStream'
import { cn } from '@/lib/utils'
import type { SocialSnapshot } from '@/lib/types'
import CrowdSentimentBar from './CrowdSentimentBar'
import ResearchBrief from './ResearchBrief'
import { attentionLabel, compact, rankDelta, signedPct } from './format'

const MentionSparkline = lazy(() => import('./MentionSparkline'))

function Heading({ children, aside }: { children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/70">{children}</h3>
      {aside}
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <p className="text-[10px] text-muted-foreground/60">{label}</p>
      <p className={cn('text-[13px] font-medium tabular-nums text-foreground', tone)}>{value}</p>
    </div>
  )
}

function Attention({ snapshot }: { snapshot: SocialSnapshot }) {
  const reddit = snapshot.reddit
  if (!reddit || !reddit.available) {
    return <p className="text-[11px] text-muted-foreground/70">{reddit?.caveat ?? 'Reddit data is not available.'}</p>
  }
  const delta = rankDelta(reddit.rank_change)
  const attention = attentionLabel(reddit.attention_z, reddit.history_days)
  return (
    <div className="space-y-2">
      {reddit.rank === null ? (
        <p className="text-[11px] text-muted-foreground/70">{reddit.caveat}</p>
      ) : (
        <div className="grid grid-cols-4 gap-2">
          <Stat label="Rank" value={`#${reddit.rank}`} />
          <Stat label="24h move" value={delta.text}
                tone={delta.tone === 'up' ? 'text-emerald-400' : delta.tone === 'down' ? 'text-rose-400' : undefined} />
          <Stat label="Mentions" value={`${compact(reddit.mentions)} (${signedPct(reddit.mention_change_pct)})`} />
          <Stat label="vs baseline" value={attention.text} tone={attention.emphasis ? 'text-sky-300' : undefined} />
        </div>
      )}
      {reddit.rank !== null && reddit.caveat && (
        <p className="text-[10px] text-amber-400/80">{reddit.caveat}</p>
      )}
      <Suspense fallback={<div className="h-20" />}>
        <MentionSparkline points={reddit.history} />
      </Suspense>
    </div>
  )
}

function Positioning({ snapshot }: { snapshot: SocialSnapshot }) {
  const p = snapshot.positioning
  if (p.short_percent_float === null && p.short_ratio === null) {
    return <p className="text-[11px] text-muted-foreground/70">No short-interest data for this ticker.</p>
  }
  return (
    <div className="grid grid-cols-3 gap-2">
      <Stat label="Short % float" value={p.short_percent_float !== null ? `${p.short_percent_float.toFixed(1)}%` : '—'}
            tone={(p.short_percent_float ?? 0) >= 20 ? 'text-sky-300' : undefined} />
      <Stat label="Days to cover" value={p.short_ratio !== null ? p.short_ratio.toFixed(1) : '—'} />
      <Stat label="vs prior month" value={signedPct(p.short_change_pct)} />
    </div>
  )
}

function Explanation({ symbol }: { symbol: string }) {
  const [requested, setRequested] = useState(false)
  const res = useCachedResource(
    requested ? K.socialExplain(symbol) : null,
    () => api.signals.explain(symbol),
    { ttl: TTL.socialExplain, enabled: requested, shouldCache: r => Boolean(r.explanation) },
  )
  if (!requested) {
    return (
      <button onClick={() => setRequested(true)}
              className="flex items-center gap-1.5 text-[11px] text-primary hover:underline">
        <Sparkles size={12} /> Describe the discussion in words
      </button>
    )
  }
  if (res.isLoading) {
    return <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground"><Loader2 size={12} className="animate-spin" /> Writing…</p>
  }
  const data = res.data
  if (!data?.available) {
    return <p className="text-[11px] text-muted-foreground/70">{data?.reason ?? res.error?.message}</p>
  }
  return (
    <div className="space-y-1.5">
      {data.guarded && (
        <p className="flex items-center gap-1 text-[10px] text-amber-400/90">
          <ShieldAlert size={11} /> The model’s wording didn’t pass review; this is the templated version.
        </p>
      )}
      {(data.explanation ?? '').split('\n\n').map((para, i) => (
        <p key={i} className="text-[12px] leading-relaxed text-foreground/85">{para}</p>
      ))}
    </div>
  )
}

function Research({ symbol }: { symbol: string }) {
  const { state, start, cancel } = useResearchStream(symbol)
  const [question, setQuestion] = useState('')
  const running = state.phase === 'gathering' || state.phase === 'writing'

  return (
    <div className="space-y-2">
      <form
        onSubmit={e => { e.preventDefault(); if (!running) void start(question) }}
        className="flex items-center gap-1.5"
      >
        <input
          value={question}
          onChange={e => setQuestion(e.target.value)}
          placeholder={`Why is ${symbol} in focus? (optional question)`}
          maxLength={300}
          className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-[12px] placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary"
        />
        {running ? (
          <button type="button" onClick={cancel}
                  className="h-8 shrink-0 rounded-md border border-border px-2.5 text-[12px] text-muted-foreground hover:bg-muted">
            Stop
          </button>
        ) : (
          <button type="submit"
                  className="flex h-8 shrink-0 items-center gap-1.5 rounded-md bg-primary/20 px-2.5 text-[12px] font-medium text-primary hover:bg-primary/30">
            <Search size={12} /> Research
          </button>
        )}
      </form>
      {state.phase === 'idle' && (
        <p className="text-[10px] leading-snug text-muted-foreground/60">
          Searches news, the web, filings and the crowd, then writes a brief where every point cites a source.
          About 15–30 seconds.
        </p>
      )}
      <ResearchBrief state={state} />
    </div>
  )
}

export default function TickerSignalPanel({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const navigate = useNavigate()
  const res = useCachedResource(
    K.socialTicker(symbol),
    () => api.signals.ticker(symbol),
    { ttl: TTL.socialTicker, refreshIntervalMs: TTL.socialTicker, shouldCache: s => !s.error },
  )
  const snapshot = res.data && !res.data.error ? res.data : null

  return (
    <Card>
      <CardContent className="flex flex-col gap-4 py-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="font-mono text-lg font-semibold text-foreground">{symbol}</p>
            <p className="truncate text-[11px] text-muted-foreground">
              {snapshot?.name ?? ''}{snapshot?.sector ? ` · ${snapshot.sector}` : ''}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button
              onClick={() => navigate(`/charting?ticker=${encodeURIComponent(symbol)}`)}
              title="Open in Charting"
              className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <CandlestickChart size={14} />
            </button>
            <button onClick={onClose} title="Close"
                    className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground">
              <X size={14} />
            </button>
          </div>
        </div>

        {res.isLoading && !snapshot ? (
          <div className="flex items-center gap-2 py-8 text-xs text-muted-foreground">
            <Loader2 size={14} className="animate-spin" /> Reading the crowd…
          </div>
        ) : !snapshot ? (
          <p className="text-xs text-rose-400">{res.data?.error ?? res.error?.message ?? 'Could not load this ticker.'}</p>
        ) : (
          <>
            <section className="space-y-2">
              <Heading aside={snapshot.reddit?.url && (
                <a href={snapshot.reddit.url} target="_blank" rel="noopener noreferrer"
                   className="flex items-center gap-0.5 text-[10px] text-muted-foreground/60 hover:text-foreground">
                  ApeWisdom <ExternalLink size={9} />
                </a>
              )}>Reddit attention</Heading>
              <Attention snapshot={snapshot} />
            </section>

            <section className="space-y-2">
              <Heading aside={snapshot.stocktwits?.url && (
                <a href={snapshot.stocktwits.url} target="_blank" rel="noopener noreferrer"
                   className="flex items-center gap-0.5 text-[10px] text-muted-foreground/60 hover:text-foreground">
                  StockTwits <ExternalLink size={9} />
                </a>
              )}>Crowd tags</Heading>
              <CrowdSentimentBar crowd={snapshot.stocktwits} />
            </section>

            <section className="space-y-2">
              <Heading>Positioning</Heading>
              <Positioning snapshot={snapshot} />
            </section>

            <Separator />

            <section className="-mx-3 space-y-1">
              <div className="px-3"><Heading>Crowd conditions</Heading></div>
              <SignalChips
                signalSet={snapshot.social_signal_set}
                emptyText={`Nothing unusual in the discussion of ${symbol} right now.`}
              />
            </section>

            {snapshot.technical_signal_set && (
              <section className="-mx-3 space-y-1">
                <div className="px-3"><Heading>Chart conditions</Heading></div>
                <SignalChips signalSet={snapshot.technical_signal_set} />
              </section>
            )}

            <section className="space-y-2">
              <Heading>In words</Heading>
              <Explanation symbol={symbol} />
            </section>

            <Separator />

            <section className="space-y-2">
              <Heading>Research</Heading>
              <Research symbol={symbol} />
            </section>

            <p className="text-[10px] leading-snug text-muted-foreground/50">{snapshot.disclaimer}</p>
          </>
        )}
      </CardContent>
    </Card>
  )
}
