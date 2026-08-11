/**
 * The small subset of Markdown this app's model output actually uses.
 *
 * Extracted from FinalReportView, which had it inline, so the chat panel does
 * not become a second hand-rolled renderer that drifts from the first.
 *
 * Inline bold and code are new here: the agent's fallback reply leans on
 * `**bold**` for signal labels, and unrendered asterisks read as a bug.
 * Everything beyond this — tables, links, images — is deliberately absent
 * rather than half-supported.
 */

import { cn } from '@/lib/utils'

/** Split on `**bold**` and `` `code` `` without pulling in a parser. */
function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)

  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
          return (
            <strong key={i} className="font-semibold text-foreground">
              {part.slice(2, -2)}
            </strong>
          )
        }
        if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
          return (
            <code
              key={i}
              className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] text-foreground/90"
            >
              {part.slice(1, -1)}
            </code>
          )
        }
        return part
      })}
    </>
  )
}

interface MarkdownProps {
  text: string
  className?: string
}

export default function Markdown({ text, className }: MarkdownProps) {
  const lines = text.split('\n')

  return (
    <div className={cn('space-y-2 text-sm text-foreground/80 leading-relaxed', className)}>
      {lines.map((line, i) => {
        if (line.startsWith('## ')) {
          return (
            <h3
              key={i}
              className="text-base font-semibold text-foreground mt-4 mb-1 border-b border-border pb-1"
            >
              <Inline text={line.slice(3)} />
            </h3>
          )
        }
        if (line.startsWith('# ')) {
          return (
            <h2 key={i} className="text-lg font-bold text-foreground mt-5 mb-1">
              <Inline text={line.slice(2)} />
            </h2>
          )
        }
        if (line.startsWith('- ') || line.startsWith('* ')) {
          return (
            <div key={i} className="flex gap-2">
              <span className="text-muted-foreground mt-1">&#8226;</span>
              <span>
                <Inline text={line.slice(2)} />
              </span>
            </div>
          )
        }
        if (line.trim() === '') {
          return <div key={i} className="h-1" />
        }
        return (
          <p key={i}>
            <Inline text={line} />
          </p>
        )
      })}
    </div>
  )
}
