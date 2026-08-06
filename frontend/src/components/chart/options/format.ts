/**
 * Number formatting for the options panel.
 *
 * The repo normally redefines these per file. Three components needing the
 * same four helpers is where that stops paying for itself, so they are shared
 * inside this folder only — no existing file changes, and the duplication
 * doesn't grow.
 */

export function fmt(v?: number | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return v.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function signed(v?: number | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return `${v > 0 ? '+' : ''}${fmt(v, digits)}`
}

/** A decimal rate as a percentage: 0.1825 → "18.25%". */
export function pct(v?: number | null, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return `${fmt(v * 100, digits)}%`
}

/** An already-0-to-100 value, e.g. a percentile. */
export function pts(v?: number | null, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return fmt(v, digits)
}

export function compact(v?: number | null): string {
  if (v == null || !Number.isFinite(v)) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e9) return `${fmt(v / 1e9, 1)}B`
  if (abs >= 1e6) return `${fmt(v / 1e6, 1)}M`
  if (abs >= 1e3) return `${fmt(v / 1e3, 1)}K`
  return fmt(v, 0)
}

export function tone(v?: number | null) {
  if (v == null || v === 0) return 'text-muted-foreground'
  return v > 0 ? 'text-emerald-400' : 'text-rose-400'
}

/**
 * Probability colouring, deliberately one-sided.
 *
 * A high probability of profit is not a good trade — it is a small credit
 * against a large tail — so a high reading gets normal foreground rather than
 * green. Only the genuinely coin-flip end is dimmed, and nothing here is
 * coloured as approval.
 */
export function popTone(v?: number | null) {
  if (v == null) return 'text-muted-foreground'
  return v >= 0.7 ? 'text-foreground' : 'text-muted-foreground'
}

export function shortDate(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
