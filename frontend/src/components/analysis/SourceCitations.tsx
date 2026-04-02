import { useState } from 'react'
import { ChevronDown, ChevronUp, ExternalLink, FileText } from 'lucide-react'
import type { Citation } from '../../lib/types'

const SOURCE_TYPE_COLORS: Record<string, string> = {
  news: 'bg-blue-500/20 text-blue-300',
  sec_filing: 'bg-purple-500/20 text-purple-300',
  earnings_call: 'bg-orange-500/20 text-orange-300',
  analyst_report: 'bg-cyan-500/20 text-cyan-300',
  market_data: 'bg-green-500/20 text-green-300',
  social_media: 'bg-pink-500/20 text-pink-300',
}

function sourceTypeColor(type: string): string {
  return SOURCE_TYPE_COLORS[type] ?? 'bg-slate-700 text-slate-300'
}

function formatSourceType(type: string): string {
  return type
    .split('_')
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

interface SourceCitationsProps {
  citations: Citation[]
}

export default function SourceCitations({ citations }: SourceCitationsProps) {
  const [open, setOpen] = useState(false)

  if (citations.length === 0) return null

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/60 overflow-hidden">
      {/* Header / toggle */}
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-slate-800 transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          <FileText size={15} className="text-slate-400" />
          <span className="text-sm font-medium text-slate-300">
            Sources & Citations
          </span>
          <span className="ml-1 text-xs bg-slate-700 text-slate-400 rounded-full px-2 py-0.5">
            {citations.length}
          </span>
        </div>
        {open ? (
          <ChevronUp size={16} className="text-slate-500" />
        ) : (
          <ChevronDown size={16} className="text-slate-500" />
        )}
      </button>

      {/* Citation list */}
      {open && (
        <div className="border-t border-slate-700 divide-y divide-slate-700/60">
          {citations.map((c, i) => (
            <div key={i} className="px-5 py-3 flex items-start gap-3">
              {/* Index */}
              <span className="shrink-0 mt-0.5 text-xs text-slate-600 w-5 text-right">{i + 1}.</span>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap mb-0.5">
                  {/* Source type badge */}
                  <span
                    className={[
                      'text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide shrink-0',
                      sourceTypeColor(c.source_type),
                    ].join(' ')}
                  >
                    {formatSourceType(c.source_type)}
                  </span>

                  {/* Published date */}
                  {c.published_date && (
                    <span className="text-[10px] text-slate-500">{c.published_date}</span>
                  )}
                </div>

                {/* Title + link */}
                {c.url ? (
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-blue-400 hover:text-blue-300 hover:underline inline-flex items-center gap-1 transition-colors"
                  >
                    {c.title}
                    <ExternalLink size={11} className="shrink-0" />
                  </a>
                ) : (
                  <p className="text-sm text-slate-300">{c.title}</p>
                )}

                {/* Excerpt */}
                {c.excerpt && (
                  <p className="mt-1 text-xs text-slate-500 line-clamp-2">{c.excerpt}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
