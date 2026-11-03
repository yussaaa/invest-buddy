/**
 * How far price sits from its 200-day average, in that average's own deviations.
 *
 * The ±1.5 lines are the same threshold the band on the price chart draws — by
 * construction, not by coincidence. See services/trend.py.
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { AXIS, GRID, UP, DOWN, REFERENCE_LINE, dateTicks, shortDate } from '@/components/charts/theme'
import { tooltipFor } from '@/components/charts/ChartTooltip'
import type { TrendRow } from '@/components/charts/zip'

export default function ZScoreChart({
  rows,
  threshold = 1.5,
  height = 180,
}: {
  rows: TrendRow[]
  threshold?: number
  height?: number
}) {
  const ticks = dateTicks(rows.map(r => r.date))

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} syncId="trend">
        <CartesianGrid {...GRID} />
        <XAxis dataKey="date" ticks={ticks} tickFormatter={shortDate} {...AXIS} />
        <YAxis width={46} tickFormatter={v => v.toFixed(1)} {...AXIS} />
        <Tooltip
          content={tooltipFor(row => [
            { label: 'z-score', value: row.z as number, digits: 2 },
            { label: 'Price', value: row.price as number },
            { label: '200 DMA', value: row.sma200 as number },
          ])}
        />
        <ReferenceLine y={0} stroke="rgba(148,163,184,0.3)" />
        <ReferenceLine y={threshold} {...REFERENCE_LINE} />
        <ReferenceLine y={-threshold} {...REFERENCE_LINE} />
        <Bar dataKey="z" isAnimationActive={false}>
          {rows.map((row, i) => (
            <Cell key={i} fill={(row.z ?? 0) >= 0 ? UP : DOWN} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
