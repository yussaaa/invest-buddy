/**
 * How fast each moving average is climbing, in percent per day.
 *
 * The zero line is the point of the chart: an average below it is falling, and
 * price above a falling average is a different condition from price above a
 * rising one.
 */

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { AXIS, GRID, MA_COLORS, REFERENCE_LINE, dateTicks, shortDate } from '@/components/charts/theme'
import { tooltipFor } from '@/components/charts/ChartTooltip'
import type { TrendRow } from '@/components/charts/zip'

export default function SlopeChart({ rows, height = 180 }: { rows: TrendRow[]; height?: number }) {
  const ticks = dateTicks(rows.map(r => r.date))

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} syncId="trend">
        <CartesianGrid {...GRID} />
        <XAxis dataKey="date" ticks={ticks} tickFormatter={shortDate} {...AXIS} />
        <YAxis width={46} tickFormatter={v => v.toFixed(2)} {...AXIS} />
        <Tooltip
          content={tooltipFor(row => [
            { label: '20 DMA', value: row.slope20 as number, colour: MA_COLORS[20], suffix: '%/day', digits: 3 },
            { label: '50 DMA', value: row.slope50 as number, colour: MA_COLORS[50], suffix: '%/day', digits: 3 },
            { label: '200 DMA', value: row.slope200 as number, colour: MA_COLORS[200], suffix: '%/day', digits: 3 },
          ])}
        />
        <ReferenceLine y={0} {...REFERENCE_LINE} />
        <Line dataKey="slope20" stroke={MA_COLORS[20]} dot={false} strokeWidth={1} isAnimationActive={false} connectNulls />
        <Line dataKey="slope50" stroke={MA_COLORS[50]} dot={false} strokeWidth={1} isAnimationActive={false} connectNulls />
        <Line dataKey="slope200" stroke={MA_COLORS[200]} dot={false} strokeWidth={1.3} isAnimationActive={false} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  )
}
