/**
 * PriceChart — TradingView Lightweight Charts wrapper.
 *
 * Price series (candles / line / area) in the main pane, volume histogram in a
 * second pane, optional SMA overlays. Crosshair moves are reported upward so
 * the page can render an OHLC legend.
 */

import { useEffect, useRef } from 'react'
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from 'lightweight-charts'
import type { Candle } from '@/lib/types'

export type ChartType = 'candles' | 'line' | 'area'

const UP = '#26a69a'
const DOWN = '#ef5350'

/** Series colour per MA period.
 *
 * Exported so the toolbar's swatches read from the same source as the lines —
 * the two drifting apart would mislabel the chart rather than just look untidy.
 * Anything not listed falls back to grey, so a new period needs an entry here.
 */
export const MA_COLORS: Record<number, string> = {
  5: '#10b981',    // emerald — kept clear of the blue price line
  20: '#f0b90b',
  50: '#5b8def',
  200: '#c94fd1',
}

export const MA_FALLBACK_COLOR = '#94a3b8'

interface PriceChartProps {
  candles: Candle[]
  chartType: ChartType
  maPeriods?: number[]
  height?: number
  onHover?: (candle: Candle | null) => void
}

function sma(candles: Candle[], period: number) {
  const out: { time: Time; value: number }[] = []
  let sum = 0
  for (let i = 0; i < candles.length; i++) {
    sum += candles[i].close
    if (i >= period) sum -= candles[i - period].close
    if (i >= period - 1) {
      out.push({ time: candles[i].time as Time, value: sum / period })
    }
  }
  return out
}

export default function PriceChart({
  candles,
  chartType,
  maPeriods = [],
  height = 460,
  onHover,
}: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const priceRef = useRef<ISeriesApi<'Candlestick' | 'Line' | 'Area'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const maRefs = useRef<Map<number, ISeriesApi<'Line'>>>(new Map())
  const hoverRef = useRef(onHover)
  hoverRef.current = onHover

  // Line/area series only carry a single value, so the legend reads OHLC from
  // the source candles keyed by time.
  const byTime = useRef(new Map<string, Candle>())
  byTime.current = new Map(candles.map(c => [String(c.time), c]))

  // Create the chart once; series are rebuilt when the chart type changes.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: 'rgba(148, 163, 184, 0.9)',
        fontSize: 11,
        panes: { separatorColor: 'rgba(148, 163, 184, 0.15)' },
      },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.07)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.07)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: 'rgba(148, 163, 184, 0.4)', labelBackgroundColor: '#1e293b' },
        horzLine: { color: 'rgba(148, 163, 184, 0.4)', labelBackgroundColor: '#1e293b' },
      },
      rightPriceScale: { borderColor: 'rgba(148, 163, 184, 0.15)' },
      timeScale: { borderColor: 'rgba(148, 163, 184, 0.15)', rightOffset: 4 },
      autoSize: true,
    })
    chartRef.current = chart

    const price =
      chartType === 'candles'
        ? chart.addSeries(CandlestickSeries, {
            upColor: UP,
            downColor: DOWN,
            borderVisible: false,
            wickUpColor: UP,
            wickDownColor: DOWN,
          })
        : chartType === 'line'
          ? chart.addSeries(LineSeries, { color: '#3b82f6', lineWidth: 2 })
          : chart.addSeries(AreaSeries, {
              lineColor: '#3b82f6',
              topColor: 'rgba(59, 130, 246, 0.35)',
              bottomColor: 'rgba(59, 130, 246, 0.02)',
              lineWidth: 2,
            })
    priceRef.current = price

    const volume = chart.addSeries(
      HistogramSeries,
      { priceFormat: { type: 'volume' }, priceScaleId: '' },
      1  // its own pane below the price
    )
    volumeRef.current = volume
    chart.panes()[1]?.setHeight(90)

    const unsubscribe = chart.subscribeCrosshairMove(param => {
      if (!hoverRef.current) return
      if (!param.time) {
        hoverRef.current(null)
        return
      }
      hoverRef.current(byTime.current.get(String(param.time)) ?? null)
    })

    return () => {
      void unsubscribe
      chart.remove()
      chartRef.current = null
      priceRef.current = null
      volumeRef.current = null
      maRefs.current.clear()
    }
  }, [chartType])

  // Feed data
  useEffect(() => {
    const price = priceRef.current
    const volume = volumeRef.current
    const chart = chartRef.current
    if (!price || !volume || !chart) return

    if (chartType === 'candles') {
      price.setData(
        candles.map(c => ({
          time: c.time as Time,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }))
      )
    } else {
      price.setData(candles.map(c => ({ time: c.time as Time, value: c.close })))
    }

    volume.setData(
      candles.map(c => ({
        time: c.time as Time,
        value: c.volume,
        color: c.close >= c.open ? 'rgba(38, 166, 154, 0.4)' : 'rgba(239, 83, 80, 0.4)',
      }))
    )

    chart.timeScale().fitContent()
  }, [candles, chartType])

  // Moving averages — added and removed as the selection changes
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    for (const [period, series] of maRefs.current) {
      if (!maPeriods.includes(period)) {
        chart.removeSeries(series)
        maRefs.current.delete(period)
      }
    }

    for (const period of maPeriods) {
      if (candles.length < period) continue
      let series = maRefs.current.get(period)
      if (!series) {
        series = chart.addSeries(LineSeries, {
          color: MA_COLORS[period] ?? MA_FALLBACK_COLOR,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        maRefs.current.set(period, series)
      }
      series.setData(sma(candles, period))
    }
  }, [candles, maPeriods, chartType])

  return <div ref={containerRef} style={{ height }} className="w-full" />
}
