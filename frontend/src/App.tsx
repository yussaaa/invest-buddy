import { useCallback, useEffect, useRef, useState } from 'react'
import {
  BrowserRouter,
  Routes,
  Route,
  NavLink,
  Navigate,
  useLocation,
  useNavigate,
  useSearchParams,
} from 'react-router-dom'
import {
  BarChart2,
  CandlestickChart,
  ChevronsLeft,
  ChevronsRight,
  Clock,
  Globe2,
  List,
  Settings,
  TrendingUp,
} from 'lucide-react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Separator } from '@/components/ui/separator'
import { ScreenContextProvider } from '@/context/ScreenContext'
import { cn, readJSON } from '@/lib/utils'
import ChatLauncher from './components/chat/ChatLauncher'
import MarketWatchlist from './components/market/MarketWatchlist'
import AnalyzePage from './pages/AnalyzePage'
import ChartingPage from './pages/ChartingPage'
import MarketPage from './pages/MarketPage'
import WatchlistPage from './pages/WatchlistPage'
import HistoryPage from './pages/HistoryPage'
import SettingsPage from './pages/SettingsPage'

const NAV_ITEMS = [
  { to: '/charting', label: 'Charting', icon: CandlestickChart },
  { to: '/market', label: 'Market', icon: Globe2 },
  { to: '/analyze', label: 'Analyze', icon: TrendingUp },
  { to: '/watchlist', label: 'Watchlist', icon: List },
  { to: '/history', label: 'History', icon: Clock },
  { to: '/settings', label: 'Settings', icon: Settings },
]

const SIDEBAR_KEY = 'agent-invest-sidebar'

const SIDEBAR_MIN_WIDTH = 176
const SIDEBAR_MAX_WIDTH = 320
const SIDEBAR_DEFAULT_WIDTH = 224   // the original w-56
const SIDEBAR_RAIL_WIDTH = 56       // collapsed: room for an icon and its hit area

interface SidebarProps {
  collapsed: boolean
  width: number
  onToggle: () => void
  onResize: (width: number) => void
}

function Sidebar({ collapsed, width, onToggle, onResize }: SidebarProps) {
  // Ref-flagged so the listeners register once rather than on every drag frame.
  const resizing = useRef(false)

  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!resizing.current) return
      // A left panel measures from the viewport's left edge; the watchlist rail
      // subtracts from innerWidth because it is anchored on the right.
      onResize(Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, e.clientX)))
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
  }, [onResize])

  const toggle = (
    <button
      onClick={onToggle}
      title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      className="grid shrink-0 place-items-center h-7 w-7 rounded text-muted-foreground hover:text-foreground hover:bg-muted"
    >
      {collapsed ? <ChevronsRight size={15} /> : <ChevronsLeft size={15} />}
    </button>
  )

  return (
    // Pinned to the viewport like the watchlist rail. Without this the panel
    // stretches to the full page height on a long page, pushing anything below
    // the nav — the collapse control included — hundreds of pixels off-screen.
    <aside
      className="relative flex flex-col shrink-0 h-screen sticky top-0 bg-card border-r border-border"
      style={{ width: collapsed ? SIDEBAR_RAIL_WIDTH : width }}
    >
      {/* Header: brand plus the collapse control, kept at the top where it is
          immediately visible rather than at the far end of the panel. */}
      <div
        className={cn(
          'flex items-center py-5',
          collapsed ? 'flex-col gap-3 px-0' : 'gap-2 px-4'
        )}
      >
        <BarChart2 className="shrink-0 text-primary" size={22} />
        {!collapsed && (
          <span className="min-w-0 flex-1 truncate text-foreground font-semibold text-lg tracking-tight">
            AgentInvest
          </span>
        )}
        {toggle}
      </div>

      <Separator />

      {/* Nav links — icons stay clickable when collapsed, so navigation never
          costs more than one click regardless of the panel's state. */}
      <nav
        className={cn(
          'flex flex-col gap-1 flex-1 overflow-y-auto py-4',
          collapsed ? 'px-2' : 'px-3'
        )}
      >
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            title={collapsed ? label : undefined}
            className={({ isActive }) =>
              cn(
                'flex items-center rounded-lg text-sm font-medium transition-colors',
                collapsed ? 'justify-center px-0 py-2.5' : 'gap-3 px-3 py-2.5',
                isActive
                  ? 'bg-primary/20 text-primary'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              )
            }
          >
            <Icon size={17} className="shrink-0" />
            {!collapsed && <span className="truncate">{label}</span>}
          </NavLink>
        ))}
      </nav>

      <Separator />

      {/* Footer — hidden when collapsed, where there is no room for prose */}
      {!collapsed && (
        <div className="px-5 py-4">
          <p className="truncate text-xs text-muted-foreground/50">
            v0.1.0 · Not financial advice
          </p>
        </div>
      )}

      {/* Drag handle — hidden while collapsed, where the width is fixed */}
      {!collapsed && (
        <div
          onMouseDown={() => {
            resizing.current = true
            document.body.style.userSelect = 'none'
            document.body.style.cursor = 'col-resize'
          }}
          className="absolute right-0 top-0 z-10 h-full w-1 -mr-0.5 cursor-col-resize hover:bg-primary/40"
        />
      )}
    </aside>
  )
}

// Pages that take a ?ticker= — a watchlist click stays on the current one
// instead of yanking the user over to Analyze.
const TICKER_AWARE_PATHS = ['/charting', '/analyze']

function AppShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const selectedTicker = searchParams.get('ticker') ?? undefined
  const isMarketPage = location.pathname.startsWith('/market')

  // Nav layout is a global preference — unlike the watchlist rail there is no
  // reason for it to differ per page, so no scoping.
  const [sidebar, setSidebar] = useState(() =>
    readJSON(SIDEBAR_KEY, { collapsed: false, width: SIDEBAR_DEFAULT_WIDTH })
  )

  useEffect(() => {
    localStorage.setItem(SIDEBAR_KEY, JSON.stringify(sidebar))
  }, [sidebar])

  const setSidebarWidth = useCallback(
    (width: number) => setSidebar(prev => (prev.width === width ? prev : { ...prev, width })),
    []
  )

  function selectTicker(ticker: string) {
    const current = TICKER_AWARE_PATHS.find(p => location.pathname.startsWith(p))
    navigate(`${current ?? '/charting'}?ticker=${encodeURIComponent(ticker)}`)
  }

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <Sidebar
        collapsed={sidebar.collapsed}
        width={sidebar.width}
        onToggle={() => setSidebar(prev => ({ ...prev, collapsed: !prev.collapsed }))}
        onResize={setSidebarWidth}
      />
      <main className="flex-1 min-w-0 overflow-y-auto">
        <Routes>
          <Route path="/" element={<Navigate to="/charting" replace />} />
          <Route path="/charting" element={<ChartingPage />} />
          <Route path="/market" element={<MarketPage />} />
          <Route path="/analyze" element={<AnalyzePage />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
      <MarketWatchlist
        selectedTicker={selectedTicker}
        onSelectTicker={selectTicker}
        // The Market page is a dashboard in its own right — the rail starts
        // out of the way there, and the user's choice per page is remembered.
        scope={isMarketPage ? 'market' : 'default'}
        defaultCollapsed={isMarketPage}
      />
      {/* Global, so the agent follows the user across pages. Fixed-position,
          so it floats over the watchlist rail rather than shifting the layout. */}
      <ChatLauncher />
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <TooltipProvider>
        <ScreenContextProvider>
          <AppShell />
        </ScreenContextProvider>
      </TooltipProvider>
    </BrowserRouter>
  )
}
