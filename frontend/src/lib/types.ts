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

export interface Watchlist {
  id: string
  user_id: string
  name: string
  tickers: string[]
  notes: Record<string, string>
  created_at: string
}
