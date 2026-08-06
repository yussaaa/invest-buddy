/**
 * The chat surface itself.
 *
 * A plain fixed panel rather than components/ui/dialog: that one locks body
 * scroll and lays a full-screen scrim over the page. The whole point here is
 * that the chart stays visible and interactive — the user asks about the candle
 * they are hovering, so covering it would defeat the feature.
 */

import { useEffect, useRef, useState } from 'react'
import { RotateCcw, Send, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import SignalChips from './SignalChips'
import ChatMessageView from './ChatMessage'
import { useChatStream } from '@/hooks/useChatStream'
import { useScreenContext } from '@/context/ScreenContext'
import { cn } from '@/lib/utils'

const SUGGESTIONS = [
  "What's happening with this stock?",
  'Is this a good time to buy?',
  'Why is it moving today?',
  'What would change this picture?',
]

interface ChatPanelProps {
  open: boolean
  onClose: () => void
  ticker?: string
}

export default function ChatPanel({ open, onClose, ticker }: ChatPanelProps) {
  const { messages, signalSet, pending, send, reset } = useChatStream()
  const { getScreenContext } = useScreenContext()
  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  function submit(text: string) {
    const message = text.trim()
    if (!message || pending) return
    setDraft('')
    // Read at send time only — the ref holds the crosshair position, which
    // changes far too often to subscribe to.
    void send(message, getScreenContext())
  }

  if (!open) return null

  return (
    <div
      role="dialog"
      aria-label="Ask about this ticker"
      className={cn(
        'fixed bottom-24 right-6 z-40 flex w-[min(26rem,calc(100vw-3rem))]',
        // Stops short of the page header: the symbol search and live price sit
        // up there, and covering them on the very page the chat is about is a
        // poor trade for a few more lines of transcript.
        'max-h-[min(36rem,calc(100vh-15rem))] flex-col overflow-hidden',
        'rounded-2xl border border-border bg-card shadow-2xl',
      )}
    >
      <header className="flex items-center gap-2 border-b border-border px-3 py-2.5">
        <span className="text-sm font-semibold text-foreground">
          {ticker ? `Ask about ${ticker}` : 'Ask about the market'}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {messages.length > 0 && (
            <button
              onClick={reset}
              title="Start over"
              aria-label="Start over"
              className="grid h-7 w-7 place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <RotateCcw size={14} />
            </button>
          )}
          <button
            onClick={onClose}
            title="Close"
            aria-label="Close chat"
            className="grid h-7 w-7 place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X size={15} />
          </button>
        </div>
      </header>

      <SignalChips signalSet={signalSet} />

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {messages.length === 0 ? (
          <div className="space-y-2">
            <p className="text-[11px] leading-relaxed text-muted-foreground/70">
              I can explain what the indicators on this chart are showing, and what
              would have to change for that picture to change. I can't tell you
              whether to trade it.
            </p>
            <div className="flex flex-col gap-1">
              {SUGGESTIONS.map(text => (
                <button
                  key={text}
                  onClick={() => submit(text)}
                  className="rounded-lg border border-border/60 px-2.5 py-1.5 text-left text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"
                >
                  {text}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map(message => (
            <ChatMessageView key={message.id} message={message} />
          ))
        )}
      </div>

      <footer className="border-t border-border p-2">
        <div className="flex items-end gap-1.5">
          <Textarea
            ref={inputRef}
            rows={1}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit(draft)
              }
            }}
            placeholder={ticker ? `Ask about ${ticker}…` : 'Ask a question…'}
            className="max-h-28 min-h-[2.25rem] flex-1 resize-none py-2 text-sm"
          />
          <Button
            size="icon"
            onClick={() => submit(draft)}
            disabled={!draft.trim() || pending}
            aria-label="Send"
            className="h-9 w-9 shrink-0"
          >
            <Send size={15} />
          </Button>
        </div>
        <p className="px-1 pt-1.5 text-[10px] leading-tight text-muted-foreground/40">
          Describes past price data. Not investment advice.
        </p>
      </footer>
    </div>
  )
}
