/**
 * Daily Reddit mentions from the stored history — the baseline a spike is
 * measured against. Lazy-loaded: recharts is the heaviest thing on the page.
 */

import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { tooltipFor } from '@/components/charts/ChartTooltip'

export default function MentionSparkline({ points }: { points: { date: string; mentions: number; rank: number | null }[] }) {
  if (points.length < 2) {
    return (
      <p className="text-[11px] text-muted-foreground/60">
        History builds from an hourly snapshot; a trend appears after a couple of days.
      </p>
    )
  }
  return (
    <div className="h-20 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
          <XAxis dataKey="date" hide />
          <YAxis hide domain={[0, 'dataMax']} />
          <Tooltip
            content={tooltipFor(row => [
              { label: 'Mentions', value: row.mentions as number, digits: 0 },
              { label: 'Rank', value: (row.rank as number | null) ?? null, digits: 0 },
            ])}
          />
          <Area type="monotone" dataKey="mentions" stroke="#7dd3fc" fill="rgba(125,211,252,0.15)" strokeWidth={1.5} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
