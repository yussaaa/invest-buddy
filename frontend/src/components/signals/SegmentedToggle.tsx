/**
 * The segmented button row used across the Signals page.
 *
 * MarketPage has the same control inline for its cap tiers; it is not
 * refactored onto this here, to keep this phase from touching that page.
 */

import { cn } from '@/lib/utils'

export interface SegmentOption<T extends string> {
  value: T
  label: string
  title?: string
  disabled?: boolean
}

export default function SegmentedToggle<T extends string>({
  options,
  value,
  onChange,
  className,
}: {
  options: SegmentOption<T>[]
  value: T
  onChange: (value: T) => void
  className?: string
}) {
  return (
    <div className={cn('flex flex-wrap items-center gap-1 rounded-lg border border-border bg-card p-1', className)}>
      {options.map(option => (
        <button
          key={option.value}
          onClick={() => !option.disabled && onChange(option.value)}
          title={option.title}
          disabled={option.disabled}
          aria-pressed={value === option.value}
          className={cn(
            'h-7 rounded px-2.5 text-xs font-medium transition-colors',
            option.disabled
              ? 'cursor-not-allowed text-muted-foreground/40'
              : value === option.value
                ? 'bg-primary/20 text-primary'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
