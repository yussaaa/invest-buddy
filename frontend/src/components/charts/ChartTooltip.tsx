/** Tooltip styled like the app's Cards, for the recharts panels. */

import type { TooltipProps } from 'recharts'

interface Row {
  label: string
  value?: number | null
  colour?: string
  suffix?: string
  digits?: number
}

export function TooltipShell({ title, rows }: { title: string; rows: Row[] }) {
  return (
    <div className="rounded-md border border-border bg-card px-2.5 py-2 shadow-lg">
      <p className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground/70">{title}</p>
      {rows.map(row => (
        <div key={row.label} className="flex items-center justify-between gap-4 text-[11px]">
          <span className="flex items-center gap-1.5 text-muted-foreground">
            {row.colour && (
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: row.colour }} />
            )}
            {row.label}
          </span>
          <span className="tabular-nums text-foreground">
            {row.value == null ? '—' : row.value.toFixed(row.digits ?? 2)}
            {row.suffix ?? ''}
          </span>
        </div>
      ))}
    </div>
  )
}

/** Build a recharts content callback from a row mapper. */
export function tooltipFor(
  rows: (payload: Record<string, unknown>) => Row[]
): TooltipProps<number, string>['content'] {
  return ({ active, payload, label }) => {
    if (!active || !payload?.length) return null
    return <TooltipShell title={String(label)} rows={rows(payload[0].payload as Record<string, unknown>)} />
  }
}
