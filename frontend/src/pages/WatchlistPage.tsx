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
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
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
    <Badge variant="outline" className="flex items-center gap-1 pl-3 pr-1.5 py-1.5 text-sm font-mono font-semibold">
      {ticker}
      <button
        onClick={onAnalyze}
        title="Quick Analyze"
        className="ml-1 p-1 rounded hover:bg-blue-500/20 text-muted-foreground hover:text-primary transition-colors"
      >
        <TrendingUp size={13} />
      </button>
      <button
        onClick={onRemove}
        title="Remove"
        className="p-1 rounded hover:bg-red-500/20 text-muted-foreground hover:text-red-400 transition-colors"
      >
        <X size={13} />
      </button>
    </Badge>
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
    <Card>
      <CardContent className="pt-5 flex flex-col gap-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <List size={16} className="text-muted-foreground" />
            <h3 className="font-semibold text-foreground">{watchlist.name}</h3>
            <Badge variant="secondary" className="text-xs">
              {watchlist.tickers.length}
            </Badge>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onDelete(watchlist.id)}
            className="h-8 w-8 text-muted-foreground hover:text-red-400 hover:bg-red-500/10"
            title="Delete watchlist"
          >
            <Trash2 size={14} />
          </Button>
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
              <Input
                ref={inputRef}
                value={newTicker}
                onChange={e => setNewTicker(e.target.value.toUpperCase())}
                onKeyDown={e => {
                  if (e.key === 'Enter') handleAdd()
                  if (e.key === 'Escape') setAdding(false)
                }}
                maxLength={10}
                placeholder="TSLA"
                className="w-20 h-8 text-xs font-mono"
              />
              <Button size="sm" onClick={handleAdd} className="h-8 text-xs">
                Add
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setAdding(false)}
                className="h-8 text-xs text-muted-foreground"
              >
                Cancel
              </Button>
            </div>
          ) : (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setAdding(true)}
              className="h-8 gap-1 border-dashed text-muted-foreground"
            >
              <Plus size={12} />
              Add ticker
            </Button>
          )}
        </div>

        <p className="text-xs text-muted-foreground/60">
          Created {new Date(watchlist.created_at).toLocaleDateString()}
        </p>
      </CardContent>
    </Card>
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
          <h1 className="text-2xl font-bold text-foreground">Watchlists</h1>
          <p className="text-sm text-muted-foreground mt-1">Manage your tracked tickers and groups.</p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <FolderPlus size={15} />
          New Watchlist
        </Button>
      </div>

      {/* Create new watchlist dialog */}
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New Watchlist</DialogTitle>
            <DialogDescription>
              Create a new watchlist to track a group of tickers.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2 py-2">
            <Label htmlFor="watchlist-name">Name</Label>
            <Input
              id="watchlist-name"
              autoFocus
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreate() }}
              placeholder="e.g. Tech Growth"
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => { setCreating(false); setNewName('') }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleCreate}
              disabled={saving || !newName.trim()}
            >
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Error */}
      {error && (
        <Alert variant="destructive">
          <AlertCircle size={16} />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center gap-3 text-muted-foreground py-12 justify-center">
          <Loader2 size={20} className="animate-spin" />
          <span>Loading watchlists...</span>
        </div>
      )}

      {/* Empty state */}
      {!loading && watchlists.length === 0 && !error && (
        <div className="text-center py-16">
          <List size={40} className="mx-auto text-muted-foreground/30 mb-4" />
          <p className="text-muted-foreground font-medium">No watchlists yet</p>
          <p className="text-sm text-muted-foreground/60 mt-1">Create your first watchlist to track tickers.</p>
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
