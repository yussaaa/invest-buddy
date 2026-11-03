import { afterEach, describe, expect, it } from 'vitest'
import { clear } from '@/lib/clientCache'
import { recallView, rememberView, toRange, toSaved } from './chartView'

afterEach(() => clear())

describe('chart view memory', () => {
  it('restores a zoomed window exactly when the series is unchanged', () => {
    rememberView('AAPL:1Y', { from: 180, to: 240 }, 251)
    expect(recallView('AAPL:1Y', 251)).toEqual({ from: 180, to: 240 })
  })

  it('slides the window with the data when a bar is appended', () => {
    // Before: the window ended 10 bars short of the latest of 251.
    rememberView('AAPL:1Y', { from: 180, to: 240 }, 251)
    // After a new session the same window, anchored to the end, moves by one.
    expect(recallView('AAPL:1Y', 252)).toEqual({ from: 181, to: 241 })
  })

  it('keeps the right margin the chart draws past the latest bar', () => {
    const saved = toSaved({ from: 200, to: 254 }, 251)   // rightOffset of ~4 bars
    expect(saved.toEnd).toBe(-4)
    expect(toRange(saved, 260)).toEqual({ from: 209, to: 263 })
  })

  it('remembers "everything" as a fit, so a growing series keeps fitting', () => {
    rememberView('AAPL:1Y', { from: 0, to: 254 }, 251)
    expect(recallView('AAPL:1Y', 252)).toBeNull()
  })

  it('fits when nothing was saved for this symbol and range', () => {
    rememberView('AAPL:1Y', { from: 100, to: 200 }, 251)
    expect(recallView('MSFT:1Y', 251)).toBeNull()
    expect(recallView('AAPL:5Y', 251)).toBeNull()
  })

  it('fits rather than showing an empty window when the series got shorter', () => {
    const saved = toSaved({ from: 10, to: 40 }, 1000)    // far in the past
    expect(toRange(saved, 50)).toBeNull()
  })

  it('ignores a series too short to have a view', () => {
    rememberView('X:1Y', { from: 0, to: 0 }, 1)
    expect(recallView('X:1Y', 1)).toBeNull()
  })
})
