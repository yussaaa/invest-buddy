import { describe, expect, it } from 'vitest'
import { DEFAULT_SECTIONS, SECTION_PREFS_KEY, readSectionPrefs, writeSectionPrefs } from './sectionPrefs'

/** A Storage stand-in — no jsdom needed, which is why readJSON takes one. */
function memoryStore(seed?: string): Storage {
  const map = new Map<string, string>()
  if (seed !== undefined) map.set(SECTION_PREFS_KEY, seed)
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
    key: (i: number) => [...map.keys()][i] ?? null,
    get length() { return map.size },
  } as Storage
}

/** Storage denied entirely, as in a private-mode browser. */
const hostileStore = {
  getItem() { throw new Error('denied') },
  setItem() { throw new Error('denied') },
  removeItem() {}, clear() {}, key: () => null, length: 0,
} as unknown as Storage

describe('readSectionPrefs', () => {
  it('falls back to the defaults when nothing is stored', () => {
    expect(readSectionPrefs(memoryStore())).toEqual(DEFAULT_SECTIONS)
  })

  it('does not throw when storage is denied', () => {
    expect(readSectionPrefs(hostileStore)).toEqual(DEFAULT_SECTIONS)
  })

  it('falls back on malformed JSON rather than taking the page down', () => {
    expect(readSectionPrefs(memoryStore('{not json'))).toEqual(DEFAULT_SECTIONS)
  })

  it('applies a stored value over the default', () => {
    // Deliberately a value that differs from the default, or this asserts nothing.
    const prefs = readSectionPrefs(memoryStore(JSON.stringify({ valuation: false })))
    expect(prefs.valuation).toBe(false)
    expect(DEFAULT_SECTIONS.valuation).toBe(true)
    expect(prefs.technicals).toBe(DEFAULT_SECTIONS.technicals)
  })

  it('ignores keys that are not sections', () => {
    const prefs = readSectionPrefs(memoryStore(JSON.stringify({ nonsense: true })))
    expect(prefs).toEqual(DEFAULT_SECTIONS)
    expect('nonsense' in prefs).toBe(false)
  })

  it('ignores a value that is not a boolean', () => {
    // "false" is a truthy string; trusting the parse would flip the panel.
    const prefs = readSectionPrefs(memoryStore(JSON.stringify({ options: 'false' })))
    expect(prefs.options).toBe(DEFAULT_SECTIONS.options)
  })

  it('ignores a stored value that is not an object at all', () => {
    expect(readSectionPrefs(memoryStore('"open"'))).toEqual(DEFAULT_SECTIONS)
    expect(readSectionPrefs(memoryStore('null'))).toEqual(DEFAULT_SECTIONS)
  })

  it('round-trips through a write', () => {
    const store = memoryStore()
    const wanted = { ...DEFAULT_SECTIONS, valuation: true, options: false }
    writeSectionPrefs(wanted, store)
    expect(readSectionPrefs(store)).toEqual(wanted)
  })
})
