import { Activity, BarChart2, MessageSquare, Shield, Sigma, TrendingUp } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { AgentProgress, AgentName, AgentStatus } from '../../lib/types'

// -- Config -------------------------------------------------------------------

const AGENT_CONFIG: Record<AgentName, { label: string; icon: React.ReactNode }> = {
  market_research: { label: 'Market Research', icon: <TrendingUp size={18} /> },
  sentiment: { label: 'Sentiment', icon: <MessageSquare size={18} /> },
  fundamental: { label: 'Fundamental', icon: <BarChart2 size={18} /> },
  technical: { label: 'Technical', icon: <Activity size={18} /> },
  risk: { label: 'Risk', icon: <Shield size={18} /> },
  options: { label: 'Options', icon: <Sigma size={18} /> },
}

const STATUS_STYLES: Record<AgentStatus, { badge: string; ring: string; dot: string }> = {
  pending: {
    badge: 'bg-muted text-muted-foreground',
    ring: 'border-border',
    dot: 'bg-muted-foreground/50',
  },
  running: {
    badge: 'bg-blue-500/20 text-blue-300',
    ring: 'border-blue-500/60 ring-2 ring-blue-500/20',
    dot: 'bg-blue-400 animate-pulse',
  },
  completed: {
    badge: 'bg-green-500/20 text-green-300',
    ring: 'border-green-500/50',
    dot: 'bg-green-400',
  },
  skipped: {
    badge: 'bg-muted text-muted-foreground/50',
    ring: 'border-border',
    dot: 'bg-muted-foreground/30',
  },
}

const STATUS_LABEL: Record<AgentStatus, string> = {
  pending: 'Pending',
  running: 'Running...',
  completed: 'Completed',
  skipped: 'Skipped',
}

// -- Component ----------------------------------------------------------------

interface AgentStatusTrackerProps {
  agents: AgentProgress[]
}

function AgentCard({ agent }: { agent: AgentProgress }) {
  const config = AGENT_CONFIG[agent.name]
  const styles = STATUS_STYLES[agent.status]

  return (
    <Card
      className={cn(
        'flex-1 min-w-[110px] transition-colors',
        styles.ring
      )}
    >
      <CardContent className="flex flex-col items-center gap-2 p-4">
        {/* Icon + dot */}
        <div className="relative">
          <div className="text-muted-foreground">{config.icon}</div>
          <span
            className={cn(
              'absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full',
              styles.dot
            )}
          />
        </div>

        {/* Name */}
        <span className="text-xs font-medium text-foreground/80 text-center leading-tight">
          {config.label}
        </span>

        {/* Status badge */}
        <Badge
          variant="secondary"
          className={cn('text-[10px] uppercase tracking-wide', styles.badge)}
        >
          {STATUS_LABEL[agent.status]}
        </Badge>

        {/* Confidence */}
        {agent.status === 'completed' && typeof agent.confidence === 'number' && (
          <span className="text-[10px] text-muted-foreground">
            {Math.round(agent.confidence * 100)}% confidence
          </span>
        )}
      </CardContent>
    </Card>
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
