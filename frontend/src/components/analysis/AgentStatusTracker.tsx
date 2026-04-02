import { Activity, BarChart2, MessageSquare, Shield, TrendingUp } from 'lucide-react'
import type { AgentProgress, AgentName, AgentStatus } from '../../lib/types'

// ── Config ──────────────────────────────────────────────────────────────────

const AGENT_CONFIG: Record<AgentName, { label: string; icon: React.ReactNode }> = {
  market_research: { label: 'Market Research', icon: <TrendingUp size={18} /> },
  sentiment: { label: 'Sentiment', icon: <MessageSquare size={18} /> },
  fundamental: { label: 'Fundamental', icon: <BarChart2 size={18} /> },
  technical: { label: 'Technical', icon: <Activity size={18} /> },
  risk: { label: 'Risk', icon: <Shield size={18} /> },
}

const STATUS_STYLES: Record<AgentStatus, { badge: string; ring: string; dot: string }> = {
  pending: {
    badge: 'bg-slate-700 text-slate-400',
    ring: 'border-slate-700',
    dot: 'bg-slate-600',
  },
  running: {
    badge: 'bg-blue-500/20 text-blue-300',
    ring: 'border-blue-500/60',
    dot: 'bg-blue-400 animate-pulse',
  },
  completed: {
    badge: 'bg-green-500/20 text-green-300',
    ring: 'border-green-500/50',
    dot: 'bg-green-400',
  },
  skipped: {
    badge: 'bg-slate-700 text-slate-500',
    ring: 'border-slate-700',
    dot: 'bg-slate-600',
  },
}

const STATUS_LABEL: Record<AgentStatus, string> = {
  pending: 'Pending',
  running: 'Running…',
  completed: 'Completed',
  skipped: 'Skipped',
}

// ── Component ────────────────────────────────────────────────────────────────

interface AgentStatusTrackerProps {
  agents: AgentProgress[]
}

function AgentCard({ agent }: { agent: AgentProgress }) {
  const config = AGENT_CONFIG[agent.name]
  const styles = STATUS_STYLES[agent.status]

  return (
    <div
      className={[
        'flex flex-col items-center gap-2 p-4 rounded-xl border bg-slate-800/60 flex-1 min-w-[110px] transition-colors',
        styles.ring,
      ].join(' ')}
    >
      {/* Icon + dot */}
      <div className="relative">
        <div className="text-slate-400">{config.icon}</div>
        <span
          className={[
            'absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full',
            styles.dot,
          ].join(' ')}
        />
      </div>

      {/* Name */}
      <span className="text-xs font-medium text-slate-300 text-center leading-tight">
        {config.label}
      </span>

      {/* Status badge */}
      <span className={['text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide', styles.badge].join(' ')}>
        {STATUS_LABEL[agent.status]}
      </span>

      {/* Confidence */}
      {agent.status === 'completed' && typeof agent.confidence === 'number' && (
        <span className="text-[10px] text-slate-400">
          {Math.round(agent.confidence * 100)}% confidence
        </span>
      )}
    </div>
  )
}

export default function AgentStatusTracker({ agents }: AgentStatusTrackerProps) {
  if (agents.length === 0) return null

  return (
    <div className="flex gap-3 flex-wrap">
      {agents.map(agent => (
        <AgentCard key={agent.name} agent={agent} />
      ))}
    </div>
  )
}
