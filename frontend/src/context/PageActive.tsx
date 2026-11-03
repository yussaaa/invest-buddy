/**
 * Whether the page a component lives on is the one on screen.
 *
 * Almost always true: react-router unmounts a page when you leave it. The
 * exception is a page kept mounted while hidden so that something expensive
 * inside it survives navigation — the Market page, whose TradingView heatmap
 * is an iframe that cannot be moved or rebuilt without a 0.5–2s blank. A
 * hidden page must not keep polling, so `useCachedResource` reads this and
 * skips its interval ticks while it is false.
 */

import { createContext, useContext } from 'react'

export const PageActiveContext = createContext(true)

export function usePageActive(): boolean {
  return useContext(PageActiveContext)
}
