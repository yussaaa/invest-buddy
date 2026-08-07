/**
 * MarketWatchlist — TradingView-style right rail.
 *
 * Sections are the user's watchlists (server-backed); each one collapses
 * independently, and the whole panel collapses to a narrow rail. Clicking a
 * row selects that ticker for analysis.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  FolderPlus,
  List,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { cn, readJSON } from '@/lib/utils'
import { api } from '@/lib/api'
import type { Quote, Watchlist } from '@/lib/types'
import { useQuotes } from '@/hooks/useQuotes'

const USER_ID = 'anonymous'

const PANEL_KEY = 'agent-invest-watchlist-panel'
const SECTIONS_KEY = 'agent-invest-watchlist-collapsed'
const SEEDED_KEY = 'agent-invest-watchlist-seeded'
// Collapsed is remembered per scope: pages that want the rail out of the way
// by default shouldn't force that choice onto the pages that don't.
const PANEL_COLLAPSE_KEY = 'agent-invest-watchlist-collapsed-by-scope'

const MIN_WIDTH = 220
const MAX_WIDTH = 480
const DEFAULT_WIDTH = 352

// Columns drop out as the panel narrows, keeping the symbol readable.
const VOL_MIN_WIDTH = 336
const CHG_MIN_WIDTH = 286

// Starter sections, created once when the user has no watchlists yet.
const STARTER_SECTIONS: { name: string; tickers: string[] }[] = [
  { name: 'Market Indicator', tickers: ['SPY', 'QQQ', 'DIA', 'IWM', '^VIX'] },
  { name: 'Watchlist', tickers: ['AAPL', 'MSFT', 'NVDA', 'TSLA'] },
]

// ── formatting ────────────────────────────────────────────────────────────────

function formatPrice(v?: number): string {
  if (v == null) return '—'
  if (Math.abs(v) >= 1000) return v.toLocaleString('en-US', { maximumFractionDigits: 2 })
  if (Math.abs(v) < 1) return v.toFixed(4)
  return v.toFixed(2)
}

function formatChange(v?: number): string {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}`
}

function formatPercent(v?: number): string {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

function formatVolume(v?: number | null): string {
  if (v == null) return '—'
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(2)}K`
  return String(v)
}

function toneClass(v?: number): string {
  if (v == null || v === 0) return 'text-muted-foreground'
  return v > 0 ? 'text-emerald-400' : 'text-rose-400'
}

// Deterministic accent per symbol, so each row's badge keeps a stable colour.
const BADGE_COLORS = [
  'bg-sky-500/20 text-sky-300',
  'bg-emerald-500/20 text-emerald-300',
  'bg-amber-500/20 text-amber-300',
  'bg-violet-500/20 text-violet-300',
  'bg-rose-500/20 text-rose-300',
  'bg-teal-500/20 text-teal-300',
]

function badgeColor(symbol: string): string {
  let hash = 0
  for (let i = 0; i < symbol.length; i++) hash = (hash * 31 + symbol.charCodeAt(i)) >>> 0
  return BADGE_COLORS[hash % BADGE_COLORS.length]
}

// ── localStorage helpers ──────────────────────────────────────────────────────

// ── rows ──────────────────────────────────────────────────────────────────────

interface RowProps {
  symbol: string
  quote?: Quote
  selected: boolean
  showChange: boolean
  showVolume: boolean
  onSelect: () => void
  onRemove: () => void
}

function QuoteRow({ symbol, quote, selected, showChange, showVolume, onSelect, onRemove }: RowProps) {
  const pct = quote?.change_percent
  const label = symbol.replace(/^\^/, '')

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect()
        }
      }}
      className={cn(
        'group relative flex items-center gap-1.5 h-[34px] pl-2.5 pr-2 cursor-pointer select-none',
        'border-l-2 transition-colors',
        selected
          ? 'border-primary bg-primary/10'
          : 'border-transparent hover:bg-muted/60'
      )}
    >
      {/* Symbol */}
      <div className="flex items-center gap-1.5 min-w-0 flex-1">
        <span
          className={cn(
            'grid place-items-center h-[18px] w-[18px] shrink-0 rounded-full text-[9px] font-bold',
            badgeColor(symbol)
          )}
        >
          {label.charAt(0)}
        </span>
        <span className="truncate text-[13px] font-medium text-foreground">{label}</span>
      </div>

      {/* Numbers */}
      <span className="w-[58px] shrink-0 text-right text-[12px] tabular-nums text-foreground">
        {formatPrice(quote?.last)}
      </span>
      {showChange && (
        <span className={cn('w-[46px] shrink-0 text-right text-[12px] tabular-nums', toneClass(quote?.change))}>
          {formatChange(quote?.change)}
        </span>
      )}
      <span className={cn('w-[54px] shrink-0 text-right text-[12px] tabular-nums', toneClass(pct))}>
        {formatPercent(pct)}
      </span>
      {showVolume && (
        <span className="w-[50px] shrink-0 text-right text-[12px] tabular-nums text-muted-foreground">
          {formatVolume(quote?.volume)}
        </span>
      )}

      {/* Remove — sits over the volume column on hover */}
      <button
        onClick={e => {
          e.stopPropagation()
          onRemove()
        }}
        title={`Remove ${label}`}
        className={cn(
          'absolute right-1.5 hidden group-hover:grid place-items-center h-5 w-5 rounded',
          'bg-card text-muted-foreground hover:text-rose-400 hover:bg-rose-500/10'
        )}
      >
        <X size={12} />
      </button>
    </div>
  )
}

// ── sections ──────────────────────────────────────────────────────────────────

interface SectionProps {
  watchlist: Watchlist
  quotes: Record<string, Quote>
  collapsed: boolean
  selectedTicker?: string
  showChange: boolean
  showVolume: boolean
  onToggle: () => void
  onSelect: (ticker: string) => void
  onAddTicker: (ticker: string) => void
  onRemoveTicker: (ticker: string) => void
  onDelete: () => void
}

function Section({
  watchlist,
  quotes,
  collapsed,
  selectedTicker,
  showChange,
  showVolume,
  onToggle,
  onSelect,
  onAddTicker,
  onRemoveTicker,
  onDelete,
}: SectionProps) {
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (adding) inputRef.current?.focus()
  }, [adding])

  function commit() {
    const t = draft.trim().toUpperCase()
    setDraft('')
    setAdding(false)
    if (!t || watchlist.tickers.includes(t)) return
    onAddTicker(t)
  }

  return (
    <div className="border-b border-border/60 last:border-b-0">
      {/* Section header */}
      <div className="group flex items-center gap-1.5 h-8 px-2 bg-muted/30 hover:bg-muted/50 transition-colors">
        <button
          onClick={onToggle}
          className="flex items-center gap-1 min-w-0 flex-1 text-left"
          aria-expanded={!collapsed}
        >
          {collapsed ? (
            <ChevronRight size={13} className="shrink-0 text-muted-foreground" />
          ) : (
            <ChevronDown size={13} className="shrink-0 text-muted-foreground" />
          )}
          <span className="truncate text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {watchlist.name}
          </span>
          <span className="text-[10px] text-muted-foreground/50 tabular-nums">
            {watchlist.tickers.length}
          </span>
        </button>

        <button
          onClick={() => {
            if (collapsed) onToggle()
            setAdding(true)
          }}
          title="Add symbol"
          className="hidden group-hover:grid place-items-center h-5 w-5 rounded text-muted-foreground hover:text-primary hover:bg-primary/10"
        >
          <Plus size={13} />
        </button>
        <button
          onClick={onDelete}
          title="Delete section"
          className="hidden group-hover:grid place-items-center h-5 w-5 rounded text-muted-foreground hover:text-rose-400 hover:bg-rose-500/10"
        >
          <Trash2 size={12} />
        </button>
      </div>

      {/* Rows */}
      {!collapsed && (
        <div>
          {watchlist.tickers.map(t => (
            <QuoteRow
              key={t}
              symbol={t}
              quote={quotes[t]}
              selected={selectedTicker === t}
              showChange={showChange}
              showVolume={showVolume}
              onSelect={() => onSelect(t)}
              onRemove={() => onRemoveTicker(t)}
            />
          ))}

          {adding && (
            <div className="flex items-center gap-1.5 px-3 py-1.5">
              <Input
                ref={inputRef}
                value={draft}
                onChange={e => setDraft(e.target.value.toUpperCase())}
                onBlur={commit}
                onKeyDown={e => {
                  if (e.key === 'Enter') commit()
                  if (e.key === 'Escape') {
                    setDraft('')
                    setAdding(false)
                  }
                }}
                maxLength={12}
                placeholder="Add symbol"
                className="h-7 text-xs font-mono"
              />
            </div>
          )}

          {watchlist.tickers.length === 0 && !adding && (
            <button
              onClick={() => setAdding(true)}
              className="w-full px-3 py-2.5 text-left text-[11px] text-muted-foreground/60 hover:text-muted-foreground"
            >
              + Add a symbol
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── panel ─────────────────────────────────────────────────────────────────────

interface MarketWatchlistProps {
  selectedTicker?: string
  onSelectTicker: (ticker: string) => void
  /** Collapse state is stored under this key, so it can differ per page. */
  scope?: string
  /** Used until the user collapses or expands the panel within this scope. */
  defaultCollapsed?: boolean
}

function readCollapsed(scope: string, fallback: boolean): boolean {
  return readJSON<Record<string, boolean>>(PANEL_COLLAPSE_KEY, {})[scope] ?? fallback
}

export default function MarketWatchlist({
  selectedTicker,
  onSelectTicker,
  scope = 'default',
  defaultCollapsed = false,
}: MarketWatchlistProps) {
  const [collapsed, setCollapsed] = useState<boolean>(() => readCollapsed(scope, defaultCollapsed))
  const [width, setWidth] = useState<number>(
    () => readJSON(PANEL_KEY, { width: DEFAULT_WIDTH }).width ?? DEFAULT_WIDTH
  )

  // Moving between pages swaps in that page's own collapse preference.
  useEffect(() => {
    setCollapsed(readCollapsed(scope, defaultCollapsed))
  }, [scope, defaultCollapsed])
  const [collapsedSections, setCollapsedSections] = useState<string[]>(() =>
    readJSON<string[]>(SECTIONS_KEY, [])
  )

  const [watchlists, setWatchlists] = useState<Watchlist[]>([])
  const [loading, setLoading] = useState(true)
  const [addingSection, setAddingSection] = useState(false)
  const [sectionName, setSectionName] = useState('')
  const sectionInputRef = useRef<HTMLInputElement>(null)

  // Persist panel + section state
  useEffect(() => {
    localStorage.setItem(PANEL_KEY, JSON.stringify({ width }))
  }, [width])

  useEffect(() => {
    const stored = readJSON<Record<string, boolean>>(PANEL_COLLAPSE_KEY, {})
    localStorage.setItem(
      PANEL_COLLAPSE_KEY,
      JSON.stringify({ ...stored, [scope]: collapsed })
    )
  }, [scope, collapsed])

  useEffect(() => {
    localStorage.setItem(SECTIONS_KEY, JSON.stringify(collapsedSections))
  }, [collapsedSections])

  useEffect(() => {
    if (addingSection) sectionInputRef.current?.focus()
  }, [addingSection])

  // Load watchlists, seeding starter sections on a first-ever empty account.
  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const lists = await api.watchlist.getAll(USER_ID)
        if (cancelled) return

        if (lists.length === 0 && localStorage.getItem(SEEDED_KEY) !== 'true') {
          localStorage.setItem(SEEDED_KEY, 'true')
          const created: Watchlist[] = []
          for (const s of STARTER_SECTIONS) {
            created.push(await api.watchlist.create(USER_ID, s.name, s.tickers))
          }
          if (!cancelled) setWatchlists(created)
        } else {
          setWatchlists(lists)
        }
      } catch {
        // Backend/DB unavailable — the panel still renders, just empty.
        if (!cancelled) setWatchlists([])
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [])

  const symbols = useMemo(
    () => Array.from(new Set(watchlists.flatMap(w => w.tickers))),
    [watchlists]
  )
  const { quotes, loading: quotesLoading, updatedAt, refresh } = useQuotes(symbols)

  const showVolume = width >= VOL_MIN_WIDTH
  const showChange = width >= CHG_MIN_WIDTH

  // Drag-to-resize
  const resizing = useRef(false)
  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!resizing.current) return
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, window.innerWidth - e.clientX))
      setWidth(next)
    }
    function onUp() {
      if (!resizing.current) return
      resizing.current = false
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [])

  function toggleSection(id: string) {
    setCollapsedSections(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    )
  }

  async function persist(id: string, tickers: string[]) {
    // Optimistic — the row list should react instantly to a click.
    setWatchlists(prev => prev.map(w => (w.id === id ? { ...w, tickers } : w)))
    try {
      const saved = await api.watchlist.update(id, { tickers })
      setWatchlists(prev => prev.map(w => (w.id === id ? saved : w)))
    } catch {
      // Reload from server on failure so the UI doesn't drift.
      try {
        setWatchlists(await api.watchlist.getAll(USER_ID))
      } catch { /* keep optimistic state */ }
    }
  }

  function addTicker(id: string, ticker: string) {
    const wl = watchlists.find(w => w.id === id)
    if (!wl || wl.tickers.includes(ticker)) return
    persist(id, [...wl.tickers, ticker])
  }

  function removeTicker(id: string, ticker: string) {
    const wl = watchlists.find(w => w.id === id)
    if (!wl) return
    persist(id, wl.tickers.filter(t => t !== ticker))
  }

  async function createSection() {
    const name = sectionName.trim()
    setSectionName('')
    setAddingSection(false)
    if (!name) return
    try {
      const wl = await api.watchlist.create(USER_ID, name, [])
      setWatchlists(prev => [...prev, wl])
    } catch { /* ignore — backend unavailable */ }
  }

  async function deleteSection(id: string) {
    setWatchlists(prev => prev.filter(w => w.id !== id))
    try {
      await api.watchlist.delete(id)
    } catch { /* ignore */ }
  }

  // Collapsed rail
  if (collapsed) {
    return (
      <aside className="flex flex-col items-center w-11 shrink-0 border-l border-border bg-card py-3 gap-3">
        <button
          onClick={() => setCollapsed(false)}
          title="Expand watchlist"
          className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:text-foreground hover:bg-muted"
        >
          <ChevronsLeft size={15} />
        </button>
        <List size={15} className="text-muted-foreground/60" />
        <span
          className="text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground/60"
          style={{ writingMode: 'vertical-rl' }}
        >
          Watchlist
        </span>
      </aside>
    )
  }

  return (
    <aside
      className="relative flex flex-col shrink-0 border-l border-border bg-card h-screen sticky top-0"
      style={{ width }}
    >
      {/* Resize handle */}
      <div
        onMouseDown={() => {
          resizing.current = true
          document.body.style.userSelect = 'none'
          document.body.style.cursor = 'col-resize'
        }}
        className="absolute left-0 top-0 h-full w-1 -ml-0.5 cursor-col-resize hover:bg-primary/40 z-10"
      />

      {/* Panel header */}
      <div className="flex items-center gap-1 h-11 px-2.5 border-b border-border shrink-0">
        <List size={14} className="text-muted-foreground" />
        <span className="flex-1 text-[13px] font-semibold text-foreground">Watchlist</span>

        <button
          onClick={refresh}
          title="Refresh quotes"
          className="grid place-items-center h-6 w-6 rounded text-muted-foreground hover:text-foreground hover:bg-muted"
        >
          <RefreshCw size={13} className={cn(quotesLoading && 'animate-spin')} />
        </button>
        <button
          onClick={() => setAddingSection(true)}
          title="New section"
          className="grid place-items-center h-6 w-6 rounded text-muted-foreground hover:text-foreground hover:bg-muted"
        >
          <FolderPlus size={13} />
        </button>
        <button
          onClick={() => setCollapsed(true)}
          title="Collapse panel"
          className="grid place-items-center h-6 w-6 rounded text-muted-foreground hover:text-foreground hover:bg-muted"
        >
          <ChevronsRight size={14} />
        </button>
      </div>

      {/* Column headers */}
      <div className="flex items-center gap-1.5 h-7 pl-[16px] pr-2 border-b border-border text-[10px] uppercase tracking-wide text-muted-foreground/60 shrink-0">
        <span className="flex-1">Symbol</span>
        <span className="w-[58px] text-right">Last</span>
        {showChange && <span className="w-[46px] text-right">Chg</span>}
        <span className="w-[54px] text-right">Chg%</span>
        {showVolume && <span className="w-[50px] text-right">Vol</span>}
      </div>

      {/* New section input */}
      {addingSection && (
        <div className="px-2.5 py-2 border-b border-border">
          <Input
            ref={sectionInputRef}
            value={sectionName}
            onChange={e => setSectionName(e.target.value)}
            onBlur={createSection}
            onKeyDown={e => {
              if (e.key === 'Enter') createSection()
              if (e.key === 'Escape') {
                setSectionName('')
                setAddingSection(false)
              }
            }}
            placeholder="Section name"
            className="h-7 text-xs"
          />
        </div>
      )}

      {/* Sections */}
      <div className="flex-1 overflow-y-auto">
        {loading && (
          <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
            <Loader2 size={14} className="animate-spin" />
            Loading watchlist…
          </div>
        )}

        {!loading && watchlists.length === 0 && (
          <div className="px-4 py-8 text-center">
            <List size={26} className="mx-auto text-muted-foreground/25 mb-3" />
            <p className="text-xs text-muted-foreground">No sections yet</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-3 h-7 text-xs"
              onClick={() => setAddingSection(true)}
            >
              <Plus size={12} />
              New section
            </Button>
          </div>
        )}

        {watchlists.map(wl => (
          <Section
            key={wl.id}
            watchlist={wl}
            quotes={quotes}
            collapsed={collapsedSections.includes(wl.id)}
            selectedTicker={selectedTicker}
            showChange={showChange}
            showVolume={showVolume}
            onToggle={() => toggleSection(wl.id)}
            onSelect={onSelectTicker}
            onAddTicker={t => addTicker(wl.id, t)}
            onRemoveTicker={t => removeTicker(wl.id, t)}
            onDelete={() => deleteSection(wl.id)}
          />
        ))}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between h-7 px-2.5 border-t border-border text-[10px] text-muted-foreground/60 shrink-0">
        <span>{symbols.length} symbols</span>
        <span>
          {updatedAt ? `Updated ${new Date(updatedAt).toLocaleTimeString()}` : 'Delayed data'}
        </span>
      </div>
    </aside>
  )
}
