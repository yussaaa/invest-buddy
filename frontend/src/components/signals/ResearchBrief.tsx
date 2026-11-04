/**
 * A research brief: streaming draft first, then the verified brief.
 *
 * Citations render as superscript links into the sources list below, and the
 * links come from the backend's source book — never from model text.
 * When review replaced the draft, the page says so plainly rather than
 * silently swapping prose under the reader.
 */

import { CheckCircle2, ExternalLink, Loader2, ShieldAlert, XCircle } from 'lucide-react'
import Markdown from '@/components/ui/Markdown'
import { cn } from '@/lib/utils'
import type { ResearchState } from '@/lib/researchReducer'
import type { ResearchSource } from '@/lib/types'
import { splitCitations } from './format'

const KIND_STYLE: Record<string, string> = {
  news: 'text-blue-300',
  web: 'text-cyan-300',
  filing: 'text-purple-300',
  social: 'text-pink-300',
  market_data: 'text-green-300',
}

const TOOL_LABEL: Record<string, string> = {
  search_web: 'Web search',
  get_recent_news: 'News',
  get_sec_filings: 'SEC filings',
  get_social_sentiment: 'Social',
  get_trending_tickers: 'Reddit board',
  get_earnings_calendar: 'Earnings calendar',
  get_company_overview: 'Company profile',
}

function Cited({ text, known }: { text: string; known: Set<string> }) {
  return (
    <>
      {splitCitations(text, known).map((part, i) =>
        typeof part === 'string' ? (
          <span key={i}>{part.replace(/\*\*/g, '')}</span>
        ) : (
          <a key={i} href={`#src-${part.id}`} className="ml-0.5 align-super text-[9px] text-sky-300 hover:underline">
            {part.id}
          </a>
        ),
      )}
    </>
  )
}

function Sources({ sources }: { sources: ResearchSource[] }) {
  if (!sources.length) return null
  return (
    <ol className="space-y-1">
      {sources.map(s => (
        <li key={s.id} id={`src-${s.id}`} className="flex gap-2 text-[11px] leading-snug">
          <span className="w-6 shrink-0 font-mono text-muted-foreground/60">{s.id}</span>
          <span className={cn('w-14 shrink-0 text-[10px] uppercase', KIND_STYLE[s.kind])}>{s.kind.replace('_', ' ')}</span>
          <span className="min-w-0 flex-1 text-muted-foreground">
            {s.url ? (
              <a href={s.url} target="_blank" rel="noopener noreferrer nofollow"
                 className="inline-flex items-start gap-1 text-foreground/80 hover:text-foreground hover:underline">
                <span className="line-clamp-2">{s.title}</span>
                <ExternalLink size={10} className="mt-0.5 shrink-0" />
              </a>
            ) : (
              <span className="line-clamp-2">{s.title}</span>
            )}
            {s.publisher && <span className="text-muted-foreground/50"> · {s.publisher}</span>}
          </span>
        </li>
      ))}
    </ol>
  )
}

export default function ResearchBrief({ state }: { state: ResearchState }) {
  const { phase, brief, draft, tools, sources, replaced, error } = state

  if (phase === 'idle') return null

  if (phase === 'error') {
    return (
      <p className="flex items-start gap-1.5 text-[11px] text-rose-400">
        <XCircle size={12} className="mt-0.5 shrink-0" />
        {error}
      </p>
    )
  }

  if (phase !== 'done' || !brief) {
    return (
      <div className="space-y-2">
        <div className="flex flex-wrap gap-1.5 text-[10px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <Loader2 size={11} className="animate-spin" />
            {phase === 'writing' ? 'Writing' : 'Gathering'} · {sources.length} source{sources.length === 1 ? '' : 's'}
          </span>
          {tools.map((t, i) => (
            <span key={i} className="flex items-center gap-1 rounded border border-border px-1.5">
              {t.done
                ? (t.success ? <CheckCircle2 size={10} className="text-emerald-400" /> : <XCircle size={10} className="text-rose-400" />)
                : <Loader2 size={10} className="animate-spin" />}
              {TOOL_LABEL[t.name] ?? t.name}
            </span>
          ))}
        </div>
        {draft && <Markdown text={draft} className="text-[12px] opacity-80" />}
      </div>
    )
  }

  const known = new Set(brief.sources.map(s => s.id))
  return (
    <div className="space-y-3">
      {(brief.guarded || replaced) && (
        <div className="flex gap-1.5 rounded-lg bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-400/90">
          <ShieldAlert size={12} className="mt-0.5 shrink-0" />
          <span>
            The written draft did not pass review
            {brief.flags.length > 0 && ` (${brief.flags.filter(f => f.severity === 'error').map(f => f.flag_type.replace('_', ' ')).join(', ') || 'no citable drivers'})`}
            , so this summary is assembled from the sources directly.
          </span>
        </div>
      )}

      <p className="text-[13px] font-medium leading-snug text-foreground">{brief.headline}</p>

      {brief.drivers.length > 0 && (
        <ul className="space-y-1.5">
          {brief.drivers.map((d, i) => (
            <li key={i} className="flex gap-2 text-[12px] leading-relaxed text-foreground/85">
              <span className="mt-1 text-muted-foreground">•</span>
              <span>
                <Cited text={d.text} known={known} />
                {d.source_ids.map(id => (
                  <a key={id} href={`#src-${id}`} className="ml-0.5 align-super text-[9px] text-sky-300 hover:underline">{id}</a>
                ))}
              </span>
            </li>
          ))}
        </ul>
      )}

      {brief.crowd_read && (
        <div>
          <p className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground/60">The crowd</p>
          <p className="text-[12px] leading-relaxed text-muted-foreground">{brief.crowd_read}</p>
        </div>
      )}

      {brief.what_would_change.length > 0 && (
        <div>
          <p className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground/60">What would change the picture</p>
          <ul className="list-disc space-y-0.5 pl-4 text-[12px] text-muted-foreground">
            {brief.what_would_change.map((t, i) => <li key={i}>{t}</li>)}
          </ul>
        </div>
      )}

      {brief.unknowns.length > 0 && (
        <div>
          <p className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground/60">Not known</p>
          <ul className="list-disc space-y-0.5 pl-4 text-[12px] text-muted-foreground">
            {brief.unknowns.map((t, i) => <li key={i}>{t}</li>)}
          </ul>
        </div>
      )}

      <div>
        <p className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground/60">Sources</p>
        <Sources sources={brief.sources} />
      </div>

      <p className="text-[10px] leading-snug text-muted-foreground/50">
        {state.cached && 'Cached brief · '}
        {state.latencyMs !== null && !state.cached && `${(state.latencyMs / 1000).toFixed(0)}s · `}
        {brief.disclaimer}
      </p>
    </div>
  )
}
