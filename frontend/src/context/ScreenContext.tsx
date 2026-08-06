/**
 * What the user is currently looking at, readable on demand by the chat panel.
 *
 * Deliberately *not* reactive. PriceChart's crosshair fires onHover on every
 * mouse move, so a context value holding the hovered candle would re-render
 * every consumer at pointer frequency. Putting it in the URL is worse — it
 * churns history on each move, and the chart toolbar state is local to
 * ChartingPage on purpose.
 *
 * So the value lives in a ref and the context exposes two stable functions.
 * `publish` never re-renders anything; `getScreenContext` is read exactly once,
 * at the moment a message is sent, which is the only moment it matters.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  type ReactNode,
} from 'react'
import type { ScreenContextValue } from '@/lib/types'

interface ScreenContextApi {
  publish: (partial: Partial<ScreenContextValue>) => void
  getScreenContext: () => ScreenContextValue
}

const ScreenContextObject = createContext<ScreenContextApi | null>(null)

export function ScreenContextProvider({ children }: { children: ReactNode }) {
  const ref = useRef<ScreenContextValue>({})

  const publish = useCallback((partial: Partial<ScreenContextValue>) => {
    ref.current = { ...ref.current, ...partial }
  }, [])

  const getScreenContext = useCallback(() => ref.current, [])

  // Both members are stable, so this object never changes identity and no
  // consumer re-renders because of the provider.
  const api = useMemo(() => ({ publish, getScreenContext }), [publish, getScreenContext])

  return <ScreenContextObject.Provider value={api}>{children}</ScreenContextObject.Provider>
}

export function useScreenContext(): ScreenContextApi {
  const api = useContext(ScreenContextObject)
  if (!api) {
    // A no-op rather than a throw: the panel is global and should never be the
    // thing that breaks a page which forgot the provider.
    return { publish: () => {}, getScreenContext: () => ({}) }
  }
  return api
}
