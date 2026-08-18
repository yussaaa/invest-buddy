/**
 * Which panels on the charting page are open.
 *
 * A preference, so it belongs in local storage next to the chart toolbar's —
 * and validated on read for the same reason `readChartPrefs` validates: storage
 * is user-editable, and a malformed entry should cost you a default, not a
 * blank page.
 */

import { readJSON, writeJSON } from '@/lib/utils'

export const SECTION_PREFS_KEY = 'agent-invest.charting.sections'

export type SectionId = 'technicals' | 'trend' | 'valuation' | 'options'

export type SectionPrefs = Record<SectionId, boolean>

/**
 * Technicals and options stay open because that is how the page behaved before
 * sections existed, and a layout change should not quietly hide what people
 * were already reading. Valuation opens closed: it is the slowest panel and the
 * most opinionated, so it should be asked for.
 */
export const DEFAULT_SECTIONS: SectionPrefs = {
  technicals: true,
  trend: true,
  valuation: false,
  options: true,
}

const IDS = Object.keys(DEFAULT_SECTIONS) as SectionId[]

export function readSectionPrefs(store: Storage = localStorage): SectionPrefs {
  const stored = readJSON<unknown>(SECTION_PREFS_KEY, {}, store)
  const source = (stored && typeof stored === 'object' ? stored : {}) as Record<string, unknown>

  const out = { ...DEFAULT_SECTIONS }
  for (const id of IDS) {
    // Only a real boolean overrides the default. A string "false" is not a
    // false, and an unknown key is not a section.
    if (typeof source[id] === 'boolean') out[id] = source[id] as boolean
  }
  return out
}

export function writeSectionPrefs(prefs: SectionPrefs, store: Storage = localStorage): void {
  writeJSON(SECTION_PREFS_KEY, prefs, store)
}
