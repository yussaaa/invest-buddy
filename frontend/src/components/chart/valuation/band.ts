/** The shape of the scenario band — pure, so it is testable without a DOM. */

import type { ScenarioCase } from '@/lib/types'

/** Above this, the band is lopsided enough that a reader will wonder why. */
export const ASYMMETRY_NOTE_RATIO = 1.5

/**
 * How lopsided the band is, from its own values.
 *
 * The steps are symmetric (±5pp growth, ±1pp terminal, ∓1.5pp discount) but
 * value is convex in the discount rate — it sits in the denominator of the
 * terminal value — so the bull case stretches further above the base than the
 * bear case falls below it. Real DCF behaviour, not a bug, and worth one line.
 */
export function bandAsymmetry(
  scenarios: { case: ScenarioCase; value_per_share?: number; error?: string }[] = []
): { upside: number; downside: number; ratio: number } | null {
  const value = (c: ScenarioCase) => {
    const s = scenarios.find(x => x.case === c)
    return s && !s.error && s.value_per_share != null ? s.value_per_share : null
  }
  const bear = value('bear')
  const base = value('base')
  const bull = value('bull')
  if (bear == null || base == null || bull == null || base <= 0) return null
  const upside = bull / base - 1
  const downside = 1 - bear / base
  if (upside <= 0 || downside <= 0) return null
  return { upside, downside, ratio: upside / downside }
}
