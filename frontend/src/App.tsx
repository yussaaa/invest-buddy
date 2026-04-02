import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { BarChart2, Clock, List, Settings, TrendingUp } from 'lucide-react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import AnalyzePage from './pages/AnalyzePage'
import WatchlistPage from './pages/WatchlistPage'
import HistoryPage from './pages/HistoryPage'
import SettingsPage from './pages/SettingsPage'

const NAV_ITEMS = [
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

export default function App() {
  return (
    <BrowserRouter>
      <TooltipProvider>
        <div className="flex min-h-screen bg-background text-foreground">
          <Sidebar />
          <main className="flex-1 overflow-y-auto">
            <Routes>
              <Route path="/" element={<Navigate to="/analyze" replace />} />
              <Route path="/analyze" element={<AnalyzePage />} />
              <Route path="/watchlist" element={<WatchlistPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </main>
        </div>
      </TooltipProvider>
    </BrowserRouter>
  )
}
