/**
 * TradingViewHeatmap — TradingView's official Stock Heatmap embed.
 *
 * Real-time ticking data straight from TradingView; we only drive the index
 * and the performance window from our own toolbar. The widget script rebuilds
 * itself from scratch on every config change, so the container is wiped and
 * the script re-injected rather than mutated.
 */

import { useEffect, useRef, useState } from 'react'
import { AlertCircle } from 'lucide-react'

const SCRIPT_SRC = 'https://s3.tradingview.com/external-embedding/embed-widget-stock-heatmap.js'

interface TradingViewHeatmapProps {
  dataSource: string
  blockColor: string
  height?: number
}

export default function TradingViewHeatmap({
  dataSource,
  blockColor,
  height = 620,
}: TradingViewHeatmapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    setFailed(false)
    el.innerHTML = '<div class="tradingview-widget-container__widget"></div>'

    const script = document.createElement('script')
    script.src = SCRIPT_SRC
    script.async = true
    script.type = 'text/javascript'
    script.onerror = () => setFailed(true)
    script.innerHTML = JSON.stringify({
      dataSource,
      blockSize: 'market_cap_basic',
      blockColor,
      grouping: 'sector',
      exchanges: [],
      locale: 'en',
      colorTheme: 'dark',
      hasTopBar: false,
      isDataSetEnabled: false,
      isZoomEnabled: true,
      hasSymbolTooltip: true,
      isMonoSize: false,
      width: '100%',
      height,
    })

    el.appendChild(script)

    return () => {
      el.innerHTML = ''
    }
  }, [dataSource, blockColor, height])

  return (
    <div className="relative">
      <div
        ref={containerRef}
        className="tradingview-widget-container"
        style={{ height }}
      />

      {failed && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-center">
          <AlertCircle size={20} className="text-muted-foreground/40" />
          <p className="text-xs text-muted-foreground">Could not load the TradingView heatmap</p>
          <p className="text-[11px] text-muted-foreground/60">
            The widget needs network access to s3.tradingview.com
          </p>
        </div>
      )}
    </div>
  )
}
