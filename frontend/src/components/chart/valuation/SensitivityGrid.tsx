/**
 * Value across discount rate x terminal growth.
 *
 * The point estimate is meaningless without this. Two inputs nobody can pin
 * down move the answer more than anything the analysis actually observed, and
 * a grid says so at a glance where a paragraph would not.
 */

import { cn } from '@/lib/utils'
import { money, percent } from './format'
import type { SensitivityGrid as Grid } from '@/lib/types'

export default function SensitivityGrid({ grid, price }: { grid: Grid; price?: number }) {
  const all = grid.values.flat().filter((v): v is number => v != null)
  if (!all.length) return null
  const min = Math.min(...all)
  const max = Math.max(...all)

  /** Shade by where the cell sits in its own range, not against the price —
   *  the grid is about dispersion, and colouring by "cheap" would editorialise. */
  function shade(v: number): string {
    const t = max === min ? 0.5 : (v - min) / (max - min)
    return `rgba(148,163,184,${(0.04 + t * 0.16).toFixed(3)})`
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[420px] border-separate border-spacing-0 text-[11px]">
        <thead>
          <tr>
            <th className="px-2 py-1 text-left font-medium text-muted-foreground/60">
              Discount ↓ / terminal →
            </th>
            {grid.terminal_growths.map(g => (
              <th key={g} className="px-2 py-1 text-right font-medium tabular-nums text-muted-foreground/70">
                {percent(g)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.values.map((row, i) => (
            <tr key={grid.discount_rates[i]}>
              <td className="px-2 py-1 tabular-nums text-muted-foreground/70">
                {percent(grid.discount_rates[i])}
              </td>
              {row.map((value, j) => (
                <td
                  key={j}
                  style={value != null ? { background: shade(value) } : undefined}
                  className={cn(
                    'px-2 py-1 text-right tabular-nums',
                    value == null
                      ? 'text-muted-foreground/30'
                      : price && value >= price
                        ? 'text-emerald-400'
                        : 'text-foreground'
                  )}
                  title={value == null ? 'discount rate too close to terminal growth' : undefined}
                >
                  {value == null ? '—' : money(value, 0)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {price != null && (
        <p className="mt-1.5 px-1 text-[10px] text-muted-foreground/60">
          Green where the model clears today's {money(price)}. Blank cells are combinations where
          the discount rate sits too close to terminal growth for a perpetuity to mean anything.
        </p>
      )}
    </div>
  )
}
