import { useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Plus,
  Trash2,
  TrendingUp,
  List,
  Loader2,
  AlertCircle,
  X,
  FolderPlus,
} from 'lucide-react'
import { api } from '../lib/api'
import type { Watchlist } from '../lib/types'

const USER_ID = 'anonymous'

function TickerBadge({
  ticker,
  onRemove,
  onAnalyze,
}: {
  ticker: string
  onRemove: () => void
  onAnalyze: () => void
}) {
  return (
    <div className="flex items-center gap-1 bg-slate-700/70 border border-slate-600 rounded-lg pl-3 pr-1.5 py-1.5">
      <span className="text-sm font-mono font-semibold text-slate-200">{ticker}</span>
      <button
        onClick={onAnalyze}
        title="Quick Analyze"
        className="ml-1 p-1 rounded hover:bg-blue-500/20 text-slate-400 hover:text-blue-400 transition-colors"
      >
        <TrendingUp size={13} />
      </button>
      <button
        onClick={onRemove}
        title="Remove"
        className="p-1 rounded hover:bg-red-500/20 text-slate-500 hover:text-red-400 transition-colors"
      >
        <X size={13} />
      </button>
    </div>
  )
}

interface WatchlistCardProps {
  watchlist: Watchlist
  onDelete: (id: string) => void
  onAddTicker: (id: string, ticker: string) => void
  onRemoveTicker: (id: string, ticker: string) => void
  onAnalyze: (ticker: string) => void
}

function WatchlistCard({ watchlist, onDelete, onAddTicker, onRemoveTicker, onAnalyze }: WatchlistCardProps) {
  const [adding, setAdding] = useState(false)
  const [newTicker, setNewTicker] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  function handleAdd() {
    const t = newTicker.trim().toUpperCase()
    if (!t || watchlist.tickers.includes(t)) return
    onAddTicker(watchlist.id, t)
    setNewTicker('')
    setAdding(false)
  }

  useEffect(() => {
    if (adding) inputRef.current?.focus()
  }, [adding])

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5 flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <List size={16} className="text-slate-400" />
          <h3 className="font-semibold text-white">{watchlist.name}</h3>
          <span className="text-xs text-slate-500 bg-slate-700 rounded-full px-2 py-0.5">
            {watchlist.tickers.length}
          </span>
        </div>
        <button
          onClick={() => onDelete(watchlist.id)}
          className="p-1.5 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-colors"
          title="Delete watchlist"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {/* Tickers */}
      <div className="flex flex-wrap gap-2">
        {watchlist.tickers.map(t => (
          <TickerBadge
            key={t}
            ticker={t}
            onRemove={() => onRemoveTicker(watchlist.id, t)}
            onAnalyze={() => onAnalyze(t)}
          />
        ))}

        {/* Add ticker inline */}
        {adding ? (
          <div className="flex items-center gap-1.5">
            <input
              ref={inputRef}
              value={newTicker}
              onChange={e => setNewTicker(e.target.value.toUpperCase())}
              onKeyDown={e => {
                if (e.key === 'Enter') handleAdd()
                if (e.key === 'Escape') setAdding(false)
              }}
              maxLength={10}
              placeholder="TSLA"
              className="w-20 rounded-lg border border-blue-500/60 bg-slate-900 px-2 py-1.5 text-xs font-mono text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/40"
            />
            <button
              onClick={handleAdd}
              className="text-xs bg-blue-600 hover:bg-blue-500 text-white px-2 py-1.5 rounded-lg transition-colors"
            >
              Add
            </button>
            <button
              onClick={() => setAdding(false)}
              className="text-xs text-slate-400 hover:text-slate-200 px-1 py-1.5"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            onClick={() => setAdding(true)}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-dashed border-slate-600 text-xs text-slate-500 hover:text-slate-300 hover:border-slate-400 transition-colors"
          >
            <Plus size={12} />
            Add ticker
          </button>
        )}
      </div>

      <p className="text-xs text-slate-600">
        Created {new Date(watchlist.created_at).toLocaleDateString()}
      </p>
    </div>
  )
}

export default function WatchlistPage() {
  const navigate = useNavigate()
  const [watchlists, setWatchlists] = useState<Watchlist[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [saving, setSaving] = useState(false)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const data = await api.watchlist.getAll(USER_ID)
      setWatchlists(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load watchlists')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function handleCreate() {
    if (!newName.trim()) return
    setSaving(true)
    try {
      const wl = await api.watchlist.create(USER_ID, newName.trim(), [])
      setWatchlists(prev => [...prev, wl])
      setCreating(false)
      setNewName('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create watchlist')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    try {
      await api.watchlist.delete(id)
      setWatchlists(prev => prev.filter(w => w.id !== id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete')
    }
  }

  async function handleAddTicker(id: string, ticker: string) {
    const wl = watchlists.find(w => w.id === id)
    if (!wl) return
    const updated = { ...wl, tickers: [...wl.tickers, ticker] }
    try {
      const saved = await api.watchlist.update(id, { tickers: updated.tickers })
      setWatchlists(prev => prev.map(w => (w.id === id ? saved : w)))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update')
    }
  }

  async function handleRemoveTicker(id: string, ticker: string) {
    const wl = watchlists.find(w => w.id === id)
    if (!wl) return
    const updated = { ...wl, tickers: wl.tickers.filter(t => t !== ticker) }
    try {
      const saved = await api.watchlist.update(id, { tickers: updated.tickers })
      setWatchlists(prev => prev.map(w => (w.id === id ? saved : w)))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update')
    }
  }

  function handleQuickAnalyze(ticker: string) {
    navigate(`/analyze?ticker=${ticker}`)
  }

  return (
    <div className="max-w-4xl mx-auto px-6 py-8 flex flex-col gap-7">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Watchlists</h1>
          <p className="text-sm text-slate-400 mt-1">Manage your tracked tickers and groups.</p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold transition-colors"
        >
          <FolderPlus size={15} />
          New Watchlist
        </button>
      </div>

      {/* Create new watchlist form */}
      {creating && (
        <div className="rounded-xl border border-blue-500/40 bg-blue-500/5 p-5 flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-blue-300">New Watchlist</h3>
          <div className="flex gap-3">
            <input
              autoFocus
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreate(); if (e.key === 'Escape') setCreating(false) }}
              placeholder="e.g. Tech Growth"
              className="flex-1 rounded-lg border border-slate-600 bg-slate-900 px-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            />
            <button
              onClick={handleCreate}
              disabled={saving || !newName.trim()}
              className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold transition-colors disabled:opacity-50"
            >
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              Create
            </button>
            <button
              onClick={() => { setCreating(false); setNewName('') }}
              className="px-3 py-2.5 rounded-lg border border-slate-600 text-slate-400 hover:text-slate-200 text-sm transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="flex items-start gap-3 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3">
          <AlertCircle size={16} className="mt-0.5 shrink-0 text-red-400" />
          <p className="text-sm text-red-300">{error}</p>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center gap-3 text-slate-400 py-12 justify-center">
          <Loader2 size={20} className="animate-spin" />
          <span>Loading watchlists…</span>
        </div>
      )}

      {/* Empty state */}
      {!loading && watchlists.length === 0 && !error && (
        <div className="text-center py-16">
          <List size={40} className="mx-auto text-slate-700 mb-4" />
          <p className="text-slate-400 font-medium">No watchlists yet</p>
          <p className="text-sm text-slate-600 mt-1">Create your first watchlist to track tickers.</p>
        </div>
      )}

      {/* Watchlist cards */}
      {!loading && watchlists.map(wl => (
        <WatchlistCard
          key={wl.id}
          watchlist={wl}
          onDelete={handleDelete}
          onAddTicker={handleAddTicker}
          onRemoveTicker={handleRemoveTicker}
          onAnalyze={handleQuickAnalyze}
        />
      ))}
    </div>
  )
}
