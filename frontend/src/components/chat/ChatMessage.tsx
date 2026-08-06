/**
 * One turn in the transcript.
 *
 * Tool activity renders above the reply because that is when it happens — the
 * agent gathers, then answers, and showing the order truthfully explains the
 * pause the user is sitting through.
 */

import { Check, Loader2, Wrench, X } from 'lucide-react'
import Markdown from '@/components/ui/Markdown'
import { cn } from '@/lib/utils'
import type { ChatMessage as Message, ChatToolActivity } from '@/lib/types'

/** Registered tool names are not something to show a reader. */
const TOOL_LABEL: Record<string, string> = {
  compute_rsi: 'Checking RSI',
  compute_macd: 'Checking MACD',
  compute_support_resistance: 'Finding support and resistance',
  compute_historical_volatility: 'Measuring volatility',
  get_company_overview: 'Reading company profile',
  get_recent_news: 'Searching recent news',
  get_earnings_calendar: 'Checking the earnings date',
}

function ToolRow({ tool }: { tool: ChatToolActivity }) {
  const label = TOOL_LABEL[tool.name] ?? tool.name
  const done = tool.latency_ms > 0 || !tool.success

  return (
    <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground/70">
      {!done ? (
        <Loader2 size={11} className="animate-spin" />
      ) : tool.success ? (
        <Check size={11} className="text-emerald-400/70" />
      ) : (
        <X size={11} className="text-rose-400/70" />
      )}
      <span>{label}</span>
      {tool.cache_hit && <span className="text-muted-foreground/40">· cached</span>}
    </div>
  )
}

export default function ChatMessageView({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary/20 px-3 py-2 text-sm text-foreground">
          {message.content}
        </div>
      </div>
    )
  }

  const waiting = message.streaming && !message.content

  return (
    <div className="space-y-1.5">
      {message.tools && message.tools.length > 0 && (
        <div className="space-y-0.5 rounded-lg bg-muted/30 px-2.5 py-1.5">
          <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-muted-foreground/50">
            <Wrench size={10} />
            Looking things up
          </div>
          {message.tools.map((tool, i) => (
            <ToolRow key={`${tool.name}-${i}`} tool={tool} />
          ))}
        </div>
      )}

      {waiting ? (
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground/60">
          <Loader2 size={12} className="animate-spin" />
          Reading the chart…
        </div>
      ) : (
        <Markdown
          text={message.content}
          className={cn(
            'text-sm',
            message.error && 'text-rose-400/80',
            // The stream is still arriving; dim it so the difference between
            // "still writing" and "finished" is visible without a spinner.
            message.streaming && 'text-foreground/60',
          )}
        />
      )}

      {message.replaced && (
        // Being told the answer was rewritten is more honest than silently
        // swapping it, and it explains why the tone changed mid-reply.
        <p className="text-[10px] leading-relaxed text-amber-400/70">
          The first draft crossed into advice, so it was replaced with the
          conditions themselves.
        </p>
      )}

      {message.signalsCited && message.signalsCited.length > 0 && (
        <p className="text-[10px] text-muted-foreground/40">
          Based on: {message.signalsCited.join(', ')}
        </p>
      )}
    </div>
  )
}
