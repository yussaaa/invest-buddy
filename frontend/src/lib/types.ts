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

export type AgentName =
  | 'market_research'
  | 'sentiment'
  | 'fundamental'
  | 'technical'
  | 'risk'
  | 'options'
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

export interface RsiReading {
  current_rsi?: number
  period?: number
  zone?: 'overbought' | 'oversold' | 'neutral'
  interpretation?: string
  historical?: number[]
  error?: string
}

export interface MacdReading {
  macd?: number
  signal?: number
  histogram?: number
  bullish_crossover?: boolean
  bearish_crossover?: boolean
  trend?: 'bullish' | 'bearish'
  interpretation?: string
  error?: string
}

export interface MaLevel {
  window: number
  sma?: number
  available: boolean
  above?: boolean
  distance_percent?: number
  slope_percent_5d?: number
  direction?: 'rising' | 'falling'
}

export interface MaLadder {
  current_price?: number
  levels: MaLevel[]
  crosses: { fast: number; slow: number; type: 'bullish' | 'bearish' }[]
  alignment?: 'bullish' | 'bearish' | 'mixed'
  above_count?: number
  total_count?: number
  interpretation?: string
  error?: string
}

export type DrawdownStatus = 'at_high' | 'pullback' | 'correction' | 'bear_market'

export interface DrawdownProfile {
  current?: number
  high_52w?: number
  /** "today" when the live price has just set a new high above the stored bars. */
  high_52w_date?: string
  low_52w?: number
  low_52w_date?: string
  /** Negative or zero — price cannot be above its own high. */
  from_high_percent?: number
  from_low_percent?: number
  /** 0-100, where price sits between the 52-week low and high. */
  range_position?: number
  status?: DrawdownStatus
  sessions?: number
  worst?: {
    depth_percent: number
    peak_date: string
    trough_date: string
    recovered: boolean
    recovered_date?: string | null
  } | null
  error?: string
}

export interface Technicals {
  symbol: string
  rsi: RsiReading
  macd: MacdReading
  moving_averages: MaLadder
  drawdown: DrawdownProfile
  as_of?: string
  trend?: TrendSummary
}

export interface TechnicalsExplanation {
  symbol: string
  available: boolean
  explanation?: string
  model?: string
  disclaimer?: string
  reason?: string
  as_of?: string
}

// ── Options ─────────────────────────────────────────────────────────────────

export type OptionStrategyKey =
  | 'csp'
  | 'covered_call'
  | 'leaps_call'
  | 'put_credit_spread'

/** Per-row caveats. Rendered as badges; rose for the ones that change the trade. */
export type OptionQuality =
  | 'sweet_spot'
  | 'low_oi'
  | 'wide_spread'
  | 'last_price_only'
  | 'iv_unreliable'
  | 'earnings_before_expiry'
  | 'short_dte_extrapolation'

/**
 * `iv_rank_available` decides which measure the panel shows. Under the free
 * provider there is no implied-vol history to rank against, so `iv_rank` is
 * null and `iv_percentile_vs_realized` carries the reading instead. Both
 * providers return every key, so neither branch reads undefined.
 */
export interface VolatilityContext {
  atm_iv_30d?: number | null
  hv_20?: number | null
  hv_60?: number | null
  hv_252?: number | null
  iv_hv_ratio?: number | null
  iv_hv_spread?: number | null
  hv_percentile_252?: number | null
  iv_percentile_vs_realized?: number | null
  iv_rank?: number | null
  iv_percentile_252?: number | null
  iv_rank_available: boolean
  method: string
  note: string
}

export interface OptionExpirySummary {
  expiry: string
  dte: number
  call_count?: number
  put_count?: number
  atm_iv_call?: number | null
  atm_iv_put?: number | null
  atm_iv?: number | null
  total_call_oi?: number
  total_put_oi?: number
  put_call_oi_ratio?: number | null
}

export interface OptionCandidate {
  strategy: OptionStrategyKey
  contract_symbol?: string
  expiry: string
  dte: number
  strike: number
  kind: 'call' | 'put'
  bid?: number | null
  ask?: number | null
  mid: number
  iv?: number | null
  delta: number
  gamma?: number
  theta_per_day?: number
  vega?: number
  moneyness_pct?: number | null
  open_interest?: number
  volume?: number
  spread_pct?: number | null
  quality: OptionQuality[]
  earnings_before_expiry?: boolean
  pop: number
  /** Comparable within a strategy only — a spread posts the width, not the strike. */
  score: number
  score_basis: 'ann_yield_x_pop' | 'pop_per_extrinsic'

  // Cash-secured put / credit spread
  credit?: number
  collateral?: number
  return_on_capital?: number | null
  annualized_yield?: number | null
  credit_per_day?: number | null
  breakeven?: number
  discount_to_spot?: number | null
  prob_itm?: number

  // Covered call
  static_return?: number
  static_return_annualized?: number | null
  if_called_return?: number
  if_called_return_annualized?: number | null
  upside_cap_pct?: number | null
  downside_breakeven?: number
  prob_called?: number
  prob_keep_shares?: number

  // LEAPS call
  debit?: number
  intrinsic?: number
  extrinsic?: number
  extrinsic_pct_of_spot?: number | null
  effective_leverage?: number | null
  breakeven_move_pct?: number | null
  theta_drag_per_day_pct?: number | null
  long_dated?: boolean

  // Put credit spread
  long_strike?: number
  long_mid?: number
  width?: number
  max_loss?: number
  max_profit?: number
  risk_reward?: number | null
}

export interface OptionUniverseCounts {
  scanned: number
  passed_liquidity: number
  passed_moneyness: number
  ranked: number
}

export interface OptionsChain {
  symbol: string
  spot?: number
  risk_free_rate?: number
  dividend_yield?: number
  expiries: OptionExpirySummary[]
  contracts: unknown[]
  volatility?: VolatilityContext
  next_earnings?: string | null
  truncated?: boolean
  partial_expiries?: { expiry: string; reason: string }[]
  warnings?: string[]
  as_of?: string
  error?: string
}

export interface OptionsStrategies {
  symbol: string
  spot?: number
  strategy: string
  as_of?: string
  volatility?: VolatilityContext
  expiries?: OptionExpirySummary[]
  next_earnings?: string | null
  candidates: OptionCandidate[]
  universe_counts?: OptionUniverseCounts
  counts_by_strategy?: Partial<Record<OptionStrategyKey, number>>
  filters_applied?: Record<string, unknown>
  probability_model?: string
  caveats?: string[]
  disclaimer?: string
  warnings?: string[]
  error?: string
}

export interface OptionsExplanation {
  symbol: string
  strategy?: string
  available: boolean
  explanation?: string
  model?: string
  disclaimer?: string
  reason?: string
  as_of?: string
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

export type SlopeLabel =
  | 'strong_uptrend' | 'uptrend' | 'weak_uptrend' | 'flat'
  | 'weak_downtrend' | 'downtrend' | 'strong_downtrend'

export type ZLabel = 'extended_high' | 'elevated' | 'neutral' | 'depressed' | 'extended_low'

export interface SlopeReading {
  per_day_percent?: number
  annualised_percent?: number
  label?: SlopeLabel
}

/** The scalars — safe to carry in the technicals payload. See services/trend.py. */
export interface TrendSummary {
  price?: number
  reference_window?: number
  sma?: number
  distance_percent?: number
  sigma?: number
  z_score?: number
  z_label?: ZLabel
  z_band?: string
  band_sigma?: number
  band_upper?: number
  band_lower?: number
  /** Keyed by SMA window — JSON object keys are strings. */
  slopes?: Record<string, SlopeReading>
  sessions?: number
  error?: string
}

/** Column-oriented: a row per session would repeat every key name 500 times. */
export interface TrendSeries {
  dates: string[]
  price: (number | null)[]
  sma: Record<string, (number | null)[]>
  band_upper: (number | null)[]
  band_lower: (number | null)[]
  slope_per_day: Record<string, (number | null)[]>
  z_score: (number | null)[]
}

export interface TrendPayload {
  symbol: string
  range: string
  summary?: TrendSummary
  series?: TrendSeries
  distribution?: { bucket: number; count: number }[]
  as_of?: string
  error?: string
}

// ── Valuation ───────────────────────────────────────────────────────────────

export type ScenarioCase = 'bear' | 'base' | 'bull'
export type AssumptionSource = 'derived' | 'user' | 'default'

export interface CostOfEquity {
  rate: number
  raw_rate: number
  beta: number
  raw_beta: number
  /** Reported, never applied silently — an extreme beta is an input problem. */
  beta_clamped: boolean
  rate_clamped: boolean
  risk_free: number
  equity_risk_premium: number
}

export interface DcfAssumptions {
  initial_growth: number
  initial_growth_source: AssumptionSource
  terminal_growth: number
  terminal_growth_source: AssumptionSource
  discount_rate: number
  discount_rate_source: AssumptionSource
  years: number
  cost_of_equity: CostOfEquity
}

export interface DcfResult {
  value_per_share?: number
  equity_value?: number
  pv_explicit?: number
  pv_terminal?: number
  /** How much of the answer is the terminal assumption. Usually most of it. */
  terminal_value_share?: number
  implied_exit_fcf_multiple?: number
  projected_fcf?: number[]
  method?: string
  error?: string
}

export interface DcfScenario extends DcfResult {
  case: ScenarioCase
  assumptions: {
    initial_growth: number
    terminal_growth: number
    discount_rate: number
    years: number
  }
}

export interface ValuationInputs {
  price?: number
  shares_outstanding?: number
  fcf_ttm?: number
  fcf_median?: number
  fcf_base_used?: number
  fcf_base?: string
  fcf_history?: number[]
  /** Spread over the median — how much the base year is a choice. */
  fcf_dispersion?: number
  fcf_source?: 'statement' | 'info'
  net_debt?: number
  market_cap?: number
  revenue_growth?: number
  earnings_growth?: number
}

export interface AnalystTargets {
  target_mean?: number
  target_high?: number
  target_low?: number
  count?: number
}

export interface SensitivityGrid {
  discount_rates: number[]
  terminal_growths: number[]
  /** values[discountIndex][growthIndex]; null where the spread guard tripped. */
  values: (number | null)[][]
}

export interface Valuation {
  symbol: string
  method?: string
  assumptions?: DcfAssumptions
  inputs?: ValuationInputs
  /** The headline: the FCF growth today's price already assumes. */
  implied_growth?: number | null
  base_case?: DcfResult
  scenarios?: DcfScenario[]
  sensitivity?: SensitivityGrid
  analysts?: AnalystTargets
  as_of?: string
  error?: string
}

export interface Mover {
  symbol: string
  name: string
  sector?: string | null
  last?: number
  change?: number
  change_percent?: number
  volume?: number
  market_cap?: number
  exchange?: string
}

/** Cap tiers the movers screen accepts — see CAP_TIERS in market_data.py. */
export type CapTier = 'all' | 'large' | 'mid' | 'small'

export interface MarketMovers {
  cap: CapTier
  cap_label?: string
  gainers: Mover[]
  losers: Mover[]
  as_of?: string
  error?: string
}

export interface EarningsEvent {
  symbol: string
  date: string
  /** Whether the call lands before the open or after the close. */
  session?: 'before_open' | 'after_close'
  eps_estimate?: number
  revenue_estimate?: number
  /** Past weeks only — what was actually reported, and by how much it beat. */
  reported_eps?: number
  surprise_percent?: number
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
  /** Week view only: 0 is the current week, -1 last week, +1 next. */
  week_offset?: number
  /** Today in US market time — the browser clock can be a day off. */
  today?: string
  is_current_week?: boolean
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

// ── Chat agent ────────────────────────────────────────────────────────────────

/** Which way a condition leans. Never an instruction — see services/signals.py. */
export type SignalDirection = 'bullish' | 'bearish' | 'neutral'
export type SignalTimeframe = 'intraday' | 'short' | 'medium' | 'long'
export type SignalReliability = 'low' | 'medium' | 'high'
export type SignalCategory = 'momentum' | 'trend' | 'volatility' | 'level' | 'event'
export type NetBias = 'bullish' | 'bearish' | 'mixed' | 'inconclusive'

export interface Signal {
  id: string
  label: string
  category: SignalCategory
  direction: SignalDirection
  timeframe: SignalTimeframe
  strength: number
  reliability: SignalReliability
  evidence: Record<string, unknown>
  rationale: string
  invalidation: string | null
}

export interface SignalSet {
  ticker: string
  as_of: string
  signals: Signal[]
  net_bias: NetBias
  bias_score: number
  conflicts: string[]
  coverage: Record<string, boolean>
  disclaimer: string
}

/** The candle under the crosshair — the one thing the backend cannot re-derive. */
export interface HoveredBar {
  time?: string | number
  open?: number
  high?: number
  low?: number
  close?: number
  volume?: number
}

export interface ScreenContextValue {
  route?: string
  ticker?: string
  range?: string
  chart_type?: string
  ma_periods?: number[]
  hovered_bar?: HoveredBar | null
}

export interface ChatToolActivity {
  name: string
  arguments?: Record<string, unknown>
  success: boolean
  error?: string | null
  latency_ms: number
  cache_hit: boolean
}

export interface ChatFlag {
  flag_type: string
  agent: string
  detail: string
  severity: 'warning' | 'error'
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** True while tokens are still arriving. */
  streaming?: boolean
  /** The draft was rejected by the guardrail and this is the substitute. */
  replaced?: boolean
  signalsCited?: string[]
  tools?: ChatToolActivity[]
  flags?: ChatFlag[]
  error?: boolean
}

export interface ChatResponse {
  conversation_id: string
  message: {
    message_id: string
    role: 'assistant'
    content: string
    signals_cited: string[]
    flags: ChatFlag[]
    blocked: boolean
    disclaimer: string
  }
  signal_set: SignalSet | null
  tool_calls: ChatToolActivity[]
  model: string
  usage: Record<string, number>
  latency_ms: number
}
