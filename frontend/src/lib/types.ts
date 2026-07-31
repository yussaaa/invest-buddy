// Shared TypeScript types — mirrors backend Pydantic models

export interface Citation {
  source_type: string
  title: string
  url?: string
  published_date?: string
  ticker?: string
  excerpt?: string
}

export interface DataPoint {
  label: string
  value: string | number | object
  unit?: string
  as_of?: string
  citation?: Citation
}

export interface ToolCallRecord {
  tool_name: string
  arguments: Record<string, unknown>
  result_summary: string
  success: boolean
  error?: string
  latency_ms: number
  cache_hit: boolean
}

export interface AgentResult {
  agent_name: string
  findings: string
  key_data_points: DataPoint[]
  confidence: number
  data_freshness?: string
  citations: Citation[]
  caveats: string[]
  tool_calls: ToolCallRecord[]
}

export interface FinalReport {
  summary: string
  detailed_analysis: string
  overall_confidence: number
  key_risks: string[]
  key_positives: string[]
  data_as_of?: string
  disclaimer: string
  citations: Citation[]
}

export interface GuardrailFlag {
  flag_type: string
  agent: string
  detail: string
  severity: 'warning' | 'error'
}

export interface AnalysisResult {
  run_id: string
  status: 'running' | 'completed' | 'error'
  ticker: string
  query: string
  created_at: string
  result?: {
    final_report: FinalReport | null
    agent_results: Record<string, AgentResult | null>
    guardrail_flags: GuardrailFlag[]
    hallucination_score: number
    latency_breakdown: Record<string, number>
    required_agents: string[]
  }
  error?: string
}

export type AgentName = 'market_research' | 'sentiment' | 'fundamental' | 'technical' | 'risk'
export type AgentStatus = 'pending' | 'running' | 'completed' | 'skipped'

export interface AgentProgress {
  name: AgentName
  status: AgentStatus
  confidence?: number
  duration_ms?: number
}

// SSE event payloads
export interface SSEEvent {
  event: string
  data: string
}

export interface UserPreferences {
  user_id: string
  risk_tolerance: 'conservative' | 'moderate' | 'aggressive'
  investment_horizon: 'short' | 'medium' | 'long'
  preferred_sectors: string[]
  analysis_depth: 'quick' | 'standard' | 'deep'
  preferred_metrics: string[]
  recent_tickers: string[]
}

export interface Quote {
  symbol: string
  last?: number
  prev_close?: number
  change?: number
  change_percent?: number
  volume?: number | null
  error?: string
}

export interface Candle {
  time: string | number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface History {
  symbol: string
  range: string
  interval: string
  intraday: boolean
  candles: Candle[]
  period_change?: number
  period_change_percent?: number
  error?: string
}

export interface InstrumentProfile {
  symbol: string
  name: string
  exchange?: string
  currency?: string
  sector?: string
  industry?: string
  market_cap?: number
  pe_ratio?: number
  forward_pe?: number
  dividend_yield?: number
  beta?: number
  day_low?: number
  day_high?: number
  week52_low?: number
  week52_high?: number
  avg_volume?: number
  error?: string
}

export interface MarketRow {
  symbol: string
  label: string
  note?: string
  unit?: string
  last?: number
  prev_close?: number
  change?: number
  change_percent?: number
  period_change_percent?: number
  sparkline?: number[]
  error?: string
}

export interface MarketOverview {
  indices: MarketRow[]
  sectors: MarketRow[]
  macro: MarketRow[]
  breadth?: {
    sectors_advancing: number
    sectors_total: number
    best?: MarketRow
    worst?: MarketRow
  }
  as_of?: string
  error?: string
}

export interface IndexBreadth {
  index: string
  label: string
  advancing?: number
  declining?: number
  unchanged?: number
  counted?: number
  constituents?: number
  advancing_percent?: number
  declining_percent?: number
  unchanged_percent?: number
  avg_change_percent?: number
  median_change_percent?: number
  error?: string
}

export interface MarketBreadth {
  indices: IndexBreadth[]
  as_of?: string
  error?: string
}

export interface HeatmapTile {
  symbol: string
  name: string
  sector: string
  market_cap?: number
  last?: number
  change_percent: number
}

export interface Heatmap {
  index: string
  index_label: string
  range: string
  tiles: HeatmapTile[]
  universe_size?: number
  advancing?: number
  declining?: number
  as_of?: string
  error?: string
}

export interface EarningsEvent {
  symbol: string
  date: string
  eps_estimate?: number
  revenue_estimate?: number
}

export interface EconomicEvent {
  name: string
  date: string
  importance: 'high' | 'medium'
  source: string
}

export interface MarketEvents {
  week_start: string
  week_end: string
  earnings: EarningsEvent[]
  economic: {
    events: EconomicEvent[]
    available: boolean
    error?: string
  }
  error?: string
}

export interface Watchlist {
  id: string
  user_id: string
  name: string
  tickers: string[]
  notes: Record<string, string>
  created_at: string
}
