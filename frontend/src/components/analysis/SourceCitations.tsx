import { ExternalLink, FileText } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'
import { ChevronDown } from 'lucide-react'
import type { Citation } from '../../lib/types'

const SOURCE_TYPE_COLORS: Record<string, string> = {
  news: 'bg-blue-500/20 text-blue-300 hover:bg-blue-500/20',
  sec_filing: 'bg-purple-500/20 text-purple-300 hover:bg-purple-500/20',
  earnings_call: 'bg-orange-500/20 text-orange-300 hover:bg-orange-500/20',
  analyst_report: 'bg-cyan-500/20 text-cyan-300 hover:bg-cyan-500/20',
  market_data: 'bg-green-500/20 text-green-300 hover:bg-green-500/20',
  social_media: 'bg-pink-500/20 text-pink-300 hover:bg-pink-500/20',
}

function sourceTypeColor(type: string): string {
  return SOURCE_TYPE_COLORS[type] ?? 'bg-muted text-muted-foreground hover:bg-muted'
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
  if (citations.length === 0) return null

  return (
    <Card className="overflow-hidden">
      <Collapsible>
        {/* Header / toggle */}
        <CollapsibleTrigger className="w-full flex items-center justify-between px-5 py-4 hover:bg-muted/50 transition-colors text-left">
          <div className="flex items-center gap-2">
            <FileText size={15} className="text-muted-foreground" />
            <span className="text-sm font-medium text-foreground/80">
              Sources & Citations
            </span>
            <Badge variant="secondary" className="ml-1 text-xs">
              {citations.length}
            </Badge>
          </div>
          <ChevronDown size={16} className="text-muted-foreground transition-transform duration-200 [[data-state=open]_&]:rotate-180" />
        </CollapsibleTrigger>

        {/* Citation list */}
        <CollapsibleContent>
          <div className="border-t border-border divide-y divide-border/60">
            {citations.map((c, i) => (
              <div key={i} className="px-5 py-3 flex items-start gap-3">
                {/* Index */}
                <span className="shrink-0 mt-0.5 text-xs text-muted-foreground/50 w-5 text-right">{i + 1}.</span>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-0.5">
                    {/* Source type badge */}
                    <Badge
                      variant="secondary"
                      className={cn(
                        'text-[10px] font-semibold uppercase tracking-wide shrink-0',
                        sourceTypeColor(c.source_type)
                      )}
                    >
                      {formatSourceType(c.source_type)}
                    </Badge>

                    {/* Published date */}
                    {c.published_date && (
                      <span className="text-[10px] text-muted-foreground">{c.published_date}</span>
                    )}
                  </div>

                  {/* Title + link */}
                  {c.url ? (
                    <a
                      href={c.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm text-primary hover:text-primary/80 hover:underline inline-flex items-center gap-1 transition-colors"
                    >
                      {c.title}
                      <ExternalLink size={11} className="shrink-0" />
                    </a>
                  ) : (
                    <p className="text-sm text-foreground/80">{c.title}</p>
                  )}

                  {/* Excerpt */}
                  {c.excerpt && (
                    <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{c.excerpt}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  )
}
