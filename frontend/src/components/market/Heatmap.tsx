/**
 * Heatmap — finviz-style treemap: sector groups, tiles sized by market cap and
 * coloured by performance over the selected window.
 *
 * Layout comes from d3-hierarchy's squarified treemap; rendering is plain SVG
 * so the tiles can carry our own typography and hover behaviour.
 */

import { useMemo, useRef, useState, useEffect } from 'react'
import { hierarchy, treemap, treemapSquarify } from 'd3-hierarchy'
import { cn } from '@/lib/utils'
import type { HeatmapTile } from '@/lib/types'

interface HeatmapProps {
  tiles: HeatmapTile[]
  height?: number
  onSelect?: (symbol: string) => void
}

interface Node {
  name: string
  symbol?: string
  sector?: string
  value?: number
  change?: number
  children?: Node[]
}

// Finviz-style ramp: saturated red → grey → saturated green, clamped at ±3%.
function tileColor(change: number): string {
  const clamped = Math.max(-3, Math.min(3, change)) / 3
  if (Math.abs(clamped) < 0.04) return 'rgb(65, 69, 84)'
  if (clamped > 0) {
    const t = clamped
    return `rgb(${Math.round(65 - 35 * t)}, ${Math.round(69 + 100 * t)}, ${Math.round(84 - 20 * t)})`
  }
  const t = -clamped
  return `rgb(${Math.round(65 + 145 * t)}, ${Math.round(69 - 20 * t)}, ${Math.round(84 - 10 * t)})`
}

function labelSize(w: number, h: number): { symbol: number; pct: number } | null {
  const area = Math.min(w, h)
  if (w < 26 || h < 18) return null
  if (area > 70) return { symbol: 18, pct: 12 }
  if (area > 46) return { symbol: 14, pct: 10 }
  if (area > 30) return { symbol: 11, pct: 8 }
  return { symbol: 9, pct: 0 }
}

export default function Heatmap({ tiles, height = 620, onSelect }: HeatmapProps) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(1000)
  const [hover, setHover] = useState<HeatmapTile | null>(null)

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const observer = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect.width
      if (w) setWidth(Math.max(320, w))
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const root = useMemo(() => {
    const bySector = new Map<string, HeatmapTile[]>()
    for (const t of tiles) {
      const list = bySector.get(t.sector) ?? []
      list.push(t)
      bySector.set(t.sector, list)
    }

    // Fall back to equal weighting when caps are missing, so tiles never vanish.
    const data: Node = {
      name: 'root',
      children: [...bySector.entries()]
        .map(([sector, members]) => ({
          name: sector,
          children: members.map(m => ({
            name: m.symbol,
            symbol: m.symbol,
            sector,
            value: m.market_cap || 1e9,
            change: m.change_percent,
          })),
        }))
        .sort(
          (a, b) =>
            b.children.reduce((s, c) => s + (c.value ?? 0), 0) -
            a.children.reduce((s, c) => s + (c.value ?? 0), 0)
        ),
    }

    const h = hierarchy<Node>(data)
      .sum(d => d.value ?? 0)
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))

    return treemap<Node>()
      .tile(treemapSquarify)
      .size([width, height])
      .paddingOuter(2)
      .paddingTop(16)
      .paddingInner(1)
      .round(true)(h)
  }, [tiles, width, height])

  const sectors = root.children ?? []
  const byTile = useMemo(() => new Map(tiles.map(t => [t.symbol, t])), [tiles])

  return (
    <div ref={wrapRef} className="relative w-full">
      <svg width={width} height={height} className="block">
        {sectors.map(sector => (
          <g key={sector.data.name}>
            <rect
              x={sector.x0}
              y={sector.y0}
              width={Math.max(0, sector.x1 - sector.x0)}
              height={Math.max(0, sector.y1 - sector.y0)}
              fill="rgba(148, 163, 184, 0.06)"
              stroke="rgba(148, 163, 184, 0.2)"
            />
            <text
              x={sector.x0 + 5}
              y={sector.y0 + 11}
              className="fill-muted-foreground"
              style={{ fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase' }}
            >
              {sector.data.name}
            </text>

            {(sector.children ?? []).map(leaf => {
              const w = Math.max(0, leaf.x1 - leaf.x0)
              const h = Math.max(0, leaf.y1 - leaf.y0)
              const change = leaf.data.change ?? 0
              const sizes = labelSize(w, h)
              const symbol = leaf.data.symbol ?? ''

              return (
                <g
                  key={symbol}
                  onMouseEnter={() => setHover(byTile.get(symbol) ?? null)}
                  onMouseLeave={() => setHover(null)}
                  onClick={() => onSelect?.(symbol)}
                  className={onSelect ? 'cursor-pointer' : undefined}
                >
                  <rect
                    x={leaf.x0}
                    y={leaf.y0}
                    width={w}
                    height={h}
                    fill={tileColor(change)}
                    stroke="rgba(15, 23, 42, 0.8)"
                    strokeWidth={1}
                  />
                  {sizes && (
                    <text
                      x={leaf.x0 + w / 2}
                      y={leaf.y0 + h / 2}
                      textAnchor="middle"
                      className="pointer-events-none select-none fill-white"
                      style={{ fontSize: sizes.symbol, fontWeight: 600 }}
                    >
                      <tspan x={leaf.x0 + w / 2} dy={sizes.pct ? '-0.15em' : '0.35em'}>
                        {symbol}
                      </tspan>
                      {sizes.pct > 0 && (
                        <tspan
                          x={leaf.x0 + w / 2}
                          dy="1.25em"
                          style={{ fontSize: sizes.pct, fontWeight: 500 }}
                        >
                          {change > 0 ? '+' : ''}
                          {change.toFixed(2)}%
                        </tspan>
                      )}
                    </text>
                  )}
                </g>
              )
            })}
          </g>
        ))}
      </svg>

      {/* Hover detail */}
      {hover && (
        <div className="pointer-events-none absolute left-2 bottom-2 rounded-md border border-border bg-popover/95 px-3 py-2 shadow-lg">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-foreground">{hover.symbol}</span>
            <span
              className={cn(
                'text-xs font-medium tabular-nums',
                hover.change_percent > 0 ? 'text-emerald-400' : 'text-rose-400'
              )}
            >
              {hover.change_percent > 0 ? '+' : ''}
              {hover.change_percent.toFixed(2)}%
            </span>
          </div>
          <p className="text-[11px] text-muted-foreground max-w-[260px] truncate">{hover.name}</p>
          <p className="text-[10px] text-muted-foreground/60">
            {hover.sector}
            {hover.market_cap ? ` · ${(hover.market_cap / 1e9).toFixed(1)}B mkt cap` : ''}
          </p>
        </div>
      )}
    </div>
  )
}
