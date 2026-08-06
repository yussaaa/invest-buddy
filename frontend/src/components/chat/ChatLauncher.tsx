/**
 * The floating button, and the panel it owns.
 *
 * Mounted once in AppShell so it follows the user across every page. It reads
 * the ticker the same way ChartingPage does — including the AAPL default —
 * because a header saying "Ask about the market" while the chart plainly shows
 * AAPL would be its own small lie.
 */

import { useState } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { MessageCircle, X } from 'lucide-react'
import ChatPanel from './ChatPanel'
import { cn } from '@/lib/utils'

// Kept in step with ChartingPage's DEFAULT_SYMBOL: with no ?ticker= the chart
// still renders AAPL, so the agent has to agree with what is on screen.
const DEFAULT_SYMBOL = 'AAPL'
const TICKER_AWARE_PATHS = ['/charting', '/analyze']

export default function ChatLauncher() {
  const [open, setOpen] = useState(false)
  const [searchParams] = useSearchParams()
  const location = useLocation()

  const onTickerPage = TICKER_AWARE_PATHS.some(p => location.pathname.startsWith(p))
  const ticker = onTickerPage
    ? (searchParams.get('ticker') || DEFAULT_SYMBOL).toUpperCase()
    : undefined

  return (
    <>
      <ChatPanel open={open} onClose={() => setOpen(false)} ticker={ticker} />

      <button
        onClick={() => setOpen(o => !o)}
        title={open ? 'Close chat' : 'Ask about this chart'}
        aria-label={open ? 'Close chat' : 'Ask about this chart'}
        aria-expanded={open}
        className={cn(
          'fixed bottom-6 right-6 z-40 grid h-12 w-12 place-items-center rounded-full',
          'bg-primary text-primary-foreground shadow-lg transition-transform',
          'hover:scale-105 active:scale-95',
        )}
      >
        {open ? <X size={20} /> : <MessageCircle size={20} />}
      </button>
    </>
  )
}
