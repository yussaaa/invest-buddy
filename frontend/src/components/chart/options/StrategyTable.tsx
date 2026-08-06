/**
 * The ranked candidate table.
 *
 * Columns differ per strategy because the numbers that matter differ: a
 * cash-secured put is judged on yield against collateral, a LEAPS call on how
 * much time premium it pays for its delta. Forcing one column set would leave
 * half of every row blank.
 *
 * Colour is used sparingly and never as approval. Annualised yield is green
 * because it is unambiguously "more is more"; probability of profit is not,
 * because a 95% reading buys a small credit against a large tail. Nothing gets
 * a star or a checkmark.
 */

import { useMemo, useState } from 'react'
import { ArrowDown, ArrowUp } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import type { OptionCandidate, OptionQuality, OptionStrategyKey } from '@/lib/types'
import { compact, fmt, pct, popTone, shortDate, signed } from './format'

// The primitive defaults (p-4 cells, h-12 heads) are built for prose tables and
// would ship this one at roughly twice the height of the chart above it.
const CELL = 'px-2 py-1.5 text-xs tabular-nums'
const HEAD = 'h-8 px-2 text-[10px] uppercase tracking-wide whitespace-nowrap'

interface Column {
  key: string
  label: string
  /** Shown in the header tooltip — these are terms of art, not common words. */
  hint?: string
  align?: 'left' | 'right'
  render: (c: OptionCandidate) => React.ReactNode
  sort?: (c: OptionCandidate) => number
}

const FLAG_LABELS: Record<OptionQuality, { label: string; severe?: boolean }> = {
  sweet_spot: { label: 'sweet spot' },
  low_oi: { label: 'low OI' },
  wide_spread: { label: 'wide spread' },
  last_price_only: { label: 'stale price' },
  iv_unreliable: { label: 'IV unreliable' },
  earnings_before_expiry: { label: 'earnings', severe: true },
  short_dte_extrapolation: { label: 'short DTE', severe: true },
}

function Flags({ quality }: { quality: OptionQuality[] }) {
  if (!quality?.length) return <span className="text-muted-foreground/40">—</span>
  return (
    <div className="flex flex-wrap justify-end gap-1">
      {quality.map(q => {
        const meta = FLAG_LABELS[q] ?? { label: q }
        return (
          <Badge
            key={q}
            variant="secondary"
            className={cn(
              'px-1.5 py-0 text-[9px] font-normal',
              meta.severe ? 'text-rose-400' : 'text-muted-foreground'
            )}
          >
            {meta.label}
          </Badge>
        )
      })}
    </div>
  )
}

const CONTRACT: Column[] = [
  {
    key: 'expiry',
    label: 'Expiry',
    align: 'left',
    sort: c => c.dte,
    render: c => (
      <span className="flex items-baseline gap-1.5 whitespace-nowrap">
        <span className="text-foreground">{shortDate(c.expiry)}</span>
        <span className="text-[10px] text-muted-foreground/60">{c.dte}d</span>
      </span>
    ),
  },
  {
    key: 'strike',
    label: 'Strike',
    sort: c => c.strike,
    render: c => (
      <span className="whitespace-nowrap">
        <span className="text-foreground">{fmt(c.strike)}</span>
        {c.moneyness_pct != null && (
          <span className="ml-1 text-[10px] text-muted-foreground/60">
            {signed(c.moneyness_pct * 100, 1)}%
          </span>
        )}
      </span>
    ),
  },
]

const GREEKS: Column[] = [
  {
    key: 'delta',
    label: 'Δ',
    hint: 'Delta — change in option price per $1 of underlying',
    sort: c => Math.abs(c.delta),
    render: c => <span className="text-muted-foreground">{fmt(Math.abs(c.delta), 3)}</span>,
  },
  {
    key: 'iv',
    label: 'IV',
    hint: 'Implied volatility, annualised',
    sort: c => c.iv ?? 0,
    render: c => <span className="text-muted-foreground">{pct(c.iv, 0)}</span>,
  },
]

const POP: Column = {
  key: 'pop',
  label: 'POP',
  hint: 'Model-implied probability of profit at expiry, risk-neutral',
  sort: c => c.pop,
  render: c => <span className={popTone(c.pop)}>{pct(c.pop, 0)}</span>,
}

const LIQUIDITY: Column = {
  key: 'liquidity',
  label: 'OI / spread',
  hint: 'Open interest and bid-ask spread as a share of the mid',
  sort: c => c.open_interest ?? 0,
  render: c => (
    <span className="whitespace-nowrap text-muted-foreground">
      {compact(c.open_interest)}
      <span className="text-muted-foreground/40"> / </span>
      {c.spread_pct != null ? pct(c.spread_pct, 0) : '—'}
    </span>
  ),
}

const FLAGS: Column = {
  key: 'flags',
  label: 'Notes',
  render: c => <Flags quality={c.quality} />,
}

function yieldCol(
  key: string,
  label: string,
  hint: string,
  pick: (c: OptionCandidate) => number | null | undefined
): Column {
  return {
    key,
    label,
    hint,
    sort: c => pick(c) ?? 0,
    render: c => <span className="text-emerald-400">{pct(pick(c), 1)}</span>,
  }
}

const COLUMNS: Record<OptionStrategyKey, Column[]> = {
  csp: [
    ...CONTRACT,
    { key: 'credit', label: 'Credit', sort: c => c.mid, render: c => fmt(c.mid) },
    ...GREEKS,
    POP,
    yieldCol(
      'ann',
      'Ann. yield',
      'Credit over collateral, scaled to a year without compounding',
      c => c.annualized_yield
    ),
    {
      key: 'collateral',
      label: 'Collateral',
      hint: 'Cash secured against assignment: strike × 100',
      sort: c => c.collateral ?? 0,
      render: c => <span className="text-muted-foreground">{compact(c.collateral)}</span>,
    },
    {
      key: 'breakeven',
      label: 'Breakeven',
      hint: 'Strike less the credit — the price below which the position loses',
      sort: c => c.breakeven ?? 0,
      render: c => fmt(c.breakeven),
    },
    LIQUIDITY,
    FLAGS,
  ],
  covered_call: [
    ...CONTRACT,
    { key: 'credit', label: 'Credit', sort: c => c.mid, render: c => fmt(c.mid) },
    ...GREEKS,
    POP,
    yieldCol(
      'static',
      'Static',
      'Premium over spot, annualised — the return if the stock does not move',
      c => c.static_return_annualized
    ),
    yieldCol(
      'ifcalled',
      'If called',
      'Return including the move to the strike, annualised',
      c => c.if_called_return_annualized
    ),
    {
      key: 'cap',
      label: 'Upside cap',
      hint: 'How far the stock can rise before the shares are called away',
      sort: c => c.upside_cap_pct ?? 0,
      render: c => <span className="text-muted-foreground">{pct(c.upside_cap_pct, 1)}</span>,
    },
    {
      key: 'called',
      label: 'P(called)',
      hint: 'Model-implied probability of assignment at expiry',
      sort: c => c.prob_called ?? 0,
      render: c => <span className="text-muted-foreground">{pct(c.prob_called, 0)}</span>,
    },
    LIQUIDITY,
    FLAGS,
  ],
  leaps_call: [
    ...CONTRACT,
    { key: 'debit', label: 'Debit', sort: c => c.mid, render: c => fmt(c.mid) },
    ...GREEKS,
    POP,
    {
      key: 'extrinsic',
      label: 'Time value',
      hint: 'Premium above intrinsic, as a share of spot — this is what decays to zero',
      sort: c => c.extrinsic_pct_of_spot ?? 0,
      render: c => <span className="text-muted-foreground">{pct(c.extrinsic_pct_of_spot, 1)}</span>,
    },
    {
      key: 'leverage',
      label: 'Leverage',
      hint: 'Underlying exposure bought per dollar of premium: delta × spot / debit',
      sort: c => c.effective_leverage ?? 0,
      render: c => `${fmt(c.effective_leverage)}×`,
    },
    {
      key: 'bemove',
      label: 'BE move',
      hint: 'How far the stock must rise to break even at expiry',
      sort: c => c.breakeven_move_pct ?? 0,
      render: c => <span className="text-muted-foreground">{pct(c.breakeven_move_pct, 1)}</span>,
    },
    LIQUIDITY,
    FLAGS,
  ],
  put_credit_spread: [
    ...CONTRACT,
    {
      key: 'legs',
      label: 'Legs',
      hint: 'Short strike over long strike',
      sort: c => c.strike,
      render: c => (
        <span className="whitespace-nowrap text-muted-foreground">
          {fmt(c.strike, 0)} / {fmt(c.long_strike, 0)}
        </span>
      ),
    },
    { key: 'credit', label: 'Credit', sort: c => c.credit ?? 0, render: c => fmt(c.credit) },
    {
      key: 'maxloss',
      label: 'Max loss',
      hint: 'Width less the credit — the worst case, known in advance',
      sort: c => c.max_loss ?? 0,
      render: c => <span className="text-rose-400">{fmt(c.max_loss)}</span>,
    },
    POP,
    yieldCol(
      'roc',
      'ROC',
      'Credit over maximum loss, for the holding period',
      c => c.return_on_capital
    ),
    {
      key: 'breakeven',
      label: 'Breakeven',
      sort: c => c.breakeven ?? 0,
      render: c => fmt(c.breakeven),
    },
    LIQUIDITY,
    FLAGS,
  ],
}

const SCORE_LABEL: Record<string, string> = {
  ann_yield_x_pop: 'Ann. yield × POP',
  pop_per_extrinsic: 'POP ÷ time value',
}

interface Props {
  strategy: OptionStrategyKey
  candidates: OptionCandidate[]
}

export default function StrategyTable({ strategy, candidates }: Props) {
  const [sortKey, setSortKey] = useState<string>('score')
  const [desc, setDesc] = useState(true)

  const columns = COLUMNS[strategy]
  const scoreBasis = candidates[0]?.score_basis
  const scoreLabel = SCORE_LABEL[scoreBasis ?? ''] ?? 'Score'

  const sorted = useMemo(() => {
    const column = columns.find(c => c.key === sortKey)
    // Sorting client-side keeps the backend cache key stable, so re-ordering
    // the table never costs a request.
    const value = column?.sort ?? ((c: OptionCandidate) => c.score)
    return [...candidates].sort((a, b) => (desc ? value(b) - value(a) : value(a) - value(b)))
  }, [candidates, columns, sortKey, desc])

  function toggle(key: string) {
    if (key === sortKey) {
      setDesc(d => !d)
    } else {
      setSortKey(key)
      setDesc(true)
    }
  }

  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          {columns.map(col => (
            <TableHead
              key={col.key}
              title={col.hint}
              onClick={col.sort ? () => toggle(col.key) : undefined}
              className={cn(
                HEAD,
                col.align === 'left' ? 'text-left' : 'text-right',
                col.sort && 'cursor-pointer select-none hover:text-foreground'
              )}
            >
              <span className="inline-flex items-center gap-0.5">
                {col.label}
                {sortKey === col.key &&
                  (desc ? <ArrowDown size={10} /> : <ArrowUp size={10} />)}
              </span>
            </TableHead>
          ))}
          <TableHead
            title="How the screen ranks these. Comparable within a strategy only — a spread posts the width as collateral, not the whole strike."
            onClick={() => toggle('score')}
            className={cn(HEAD, 'cursor-pointer select-none text-right hover:text-foreground')}
          >
            <span className="inline-flex items-center gap-0.5">
              {scoreLabel}
              {sortKey === 'score' && (desc ? <ArrowDown size={10} /> : <ArrowUp size={10} />)}
            </span>
          </TableHead>
        </TableRow>
      </TableHeader>

      <TableBody>
        {sorted.map(c => (
          <TableRow key={`${c.strategy}-${c.expiry}-${c.strike}-${c.long_strike ?? ''}`}>
            {columns.map(col => (
              <TableCell
                key={col.key}
                className={cn(CELL, col.align === 'left' ? 'text-left' : 'text-right')}
              >
                {col.render(c)}
              </TableCell>
            ))}
            <TableCell className={cn(CELL, 'text-right text-muted-foreground')}>
              {fmt(c.score, 2)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export { SCORE_LABEL }
