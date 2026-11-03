/**
 * Price against its 200-day average, inside a ±1.5σ envelope.
 *
 * The band is why this is recharts and not lightweight-charts: filling between
 * two series is the chart's whole point, and lightweight-charts cannot do it.
 */

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { AXIS, GRID, BAND_FILL, MA_COLORS, dateTicks, shortDate } from '@/components/charts/theme'
import { tooltipFor } from '@/components/charts/ChartTooltip'
import type { TrendRow } from '@/components/charts/zip'

const PRICE_COLOUR = '#e2e8f0'

export default function PriceBandChart({ rows, height = 300 }: { rows: TrendRow[]; height?: number }) {
  const ticks = dateTicks(rows.map(r => r.date))

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} syncId="trend">
        <CartesianGrid {...GRID} />
        <XAxis dataKey="date" ticks={ticks} tickFormatter={shortDate} {...AXIS} />
        <YAxis width={46} domain={['auto', 'auto']} {...AXIS} />
        <Tooltip
          content={tooltipFor(row => [
            { label: 'Price', value: row.price as number, colour: PRICE_COLOUR },
            { label: '20 DMA', value: row.sma20 as number, colour: MA_COLORS[20] },
            { label: '50 DMA', value: row.sma50 as number, colour: MA_COLORS[50] },
            { label: '200 DMA', value: row.sma200 as number, colour: MA_COLORS[200] },
            { label: 'z-score', value: row.z as number, digits: 2 },
          ])}
        />
        {/* Drawn first so every line sits on top of it. */}
        <Area
          dataKey="band"
          stroke="none"
          fill={BAND_FILL}
          isAnimationActive={false}
          connectNulls
        />
        <Line dataKey="sma200" stroke={MA_COLORS[200]} dot={false} strokeWidth={1.2} isAnimationActive={false} connectNulls />
        <Line dataKey="sma50" stroke={MA_COLORS[50]} dot={false} strokeWidth={1} isAnimationActive={false} connectNulls />
        <Line dataKey="sma20" stroke={MA_COLORS[20]} dot={false} strokeWidth={1} isAnimationActive={false} connectNulls />
        <Line dataKey="price" stroke={PRICE_COLOUR} dot={false} strokeWidth={1.4} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
