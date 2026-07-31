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
  Clock,
  Globe2,
  List,
  Settings,
  TrendingUp,
} from 'lucide-react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
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

function Sidebar() {
  return (
    <aside className="flex flex-col w-56 shrink-0 min-h-screen bg-card border-r border-border">
      {/* Logo */}
      <div className="flex items-center gap-2 px-5 py-5">
        <BarChart2 className="text-primary" size={22} />
        <span className="text-foreground font-semibold text-lg tracking-tight">AgentInvest</span>
      </div>

      <Separator />

      {/* Nav links */}
      <nav className="flex flex-col gap-1 px-3 py-4 flex-1">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
                isActive
                  ? 'bg-primary/20 text-primary'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              )
            }
          >
            <Icon size={17} />
            {label}
          </NavLink>
        ))}
      </nav>

      <Separator />

      {/* Footer */}
      <div className="px-5 py-4">
        <p className="text-xs text-muted-foreground/50">v0.1.0 · Not financial advice</p>
      </div>
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

  function selectTicker(ticker: string) {
    const current = TICKER_AWARE_PATHS.find(p => location.pathname.startsWith(p))
    navigate(`${current ?? '/charting'}?ticker=${encodeURIComponent(ticker)}`)
  }

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <Sidebar />
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
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <TooltipProvider>
        <AppShell />
      </TooltipProvider>
    </BrowserRouter>
  )
}
