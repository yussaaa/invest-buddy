import { describe, expect, it } from 'vitest'
import { initialResearchState, researchReducer, type ResearchState } from './researchReducer'

function run(events: [string, Record<string, unknown>][]): ResearchState {
  let state = researchReducer(initialResearchState, { type: 'begin', ticker: 'PLTR', question: null })
  for (const [event, data] of events) state = researchReducer(state, { type: 'event', event, data })
  return state
}

const BRIEF = {
  ticker: 'PLTR', headline: 'h', drivers: [], sources: [{ id: 'S1' }], flags: [], guarded: false,
}

describe('researchReducer', () => {
  it('streams a draft, then replaces it with the brief', () => {
    const writing = run([['start', { cached: false }], ['token', { text: 'Pal' }], ['token', { text: 'antir' }]])
    expect(writing.phase).toBe('writing')
    expect(writing.draft).toBe('Palantir')

    const done = run([['token', { text: 'draft' }], ['done', { brief: BRIEF, replaced: false }]])
    expect(done.phase).toBe('done')
    expect(done.draft).toBe('')
    expect(done.brief?.headline).toBe('h')
  })

  it('remembers that a draft was replaced by review', () => {
    const state = run([['token', { text: 'time to buy' }], ['done', { brief: { ...BRIEF, guarded: true }, replaced: true }]])
    expect(state.replaced).toBe(true)
    expect(state.draft).toBe('')
  })

  it('pairs tool results with their calls in order', () => {
    const state = run([
      ['tool_call', { name: 'search_web' }],
      ['tool_call', { name: 'search_web' }],
      ['tool_result', { name: 'search_web', success: true }],
    ])
    expect(state.tools.map(t => t.done)).toEqual([true, false])
  })

  it('serves a cached brief straight to done', () => {
    const state = run([['start', { cached: true }], ['done', { brief: BRIEF, replaced: false, cached: true }]])
    expect(state.cached).toBe(true)
    expect(state.phase).toBe('done')
  })

  it('takes sources from the brief once it lands', () => {
    const state = run([['sources', { sources: [{ id: 'S1' }, { id: 'S2' }] }], ['done', { brief: BRIEF }]])
    expect(state.sources.map(s => s.id)).toEqual(['S1'])
  })

  it('surfaces a server error', () => {
    expect(run([['error', { message: 'nope' }]]).error).toBe('nope')
  })
})
