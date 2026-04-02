import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { BarChart2, Clock, List, Settings, TrendingUp } from 'lucide-react'
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
    <aside className="flex flex-col w-56 shrink-0 min-h-screen bg-slate-900 border-r border-slate-800">
      {/* Logo */}
      <div className="flex items-center gap-2 px-5 py-5 border-b border-slate-800">
        <BarChart2 className="text-blue-400" size={22} />
        <span className="text-white font-semibold text-lg tracking-tight">AgentInvest</span>
      </div>

      {/* Nav links */}
      <nav className="flex flex-col gap-1 px-3 py-4 flex-1">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              [
                'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
                isActive
                  ? 'bg-blue-600/20 text-blue-400'
                  : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800',
              ].join(' ')
            }
          >
            <Icon size={17} />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-slate-800">
        <p className="text-xs text-slate-600">v0.1.0 · Not financial advice</p>
      </div>
    </aside>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex min-h-screen bg-slate-950 text-slate-100">
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
    </BrowserRouter>
  )
}
