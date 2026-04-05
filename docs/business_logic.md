# Business Logic

This document describes what Agent Invest does from a product and user perspective — the flows, rules, and decisions that govern how the system behaves.

## Core User Flow

```
1. User enters a ticker (e.g., AAPL)
2. User selects an analysis type (Full, Fundamental, Technical, Risk, Sentiment, Market Research, Other)
3. Optionally: toggles "Include Document Research" (RAG) and/or uploads files
4. Optionally: adds custom instructions
5. Clicks "Analyze"
6. System shows live agent progress (5 animated status cards)
7. System returns a structured report with confidence score, key risks/positives, citations, and disclaimer
8. User can give thumbs up/down feedback
```

## Analysis Types

Each analysis type maps to a pre-built query that the backend's classifier routes to the appropriate agents:

| Type | Default Query | Agents Activated | Model Tier |
|------|--------------|------------------|------------|
| **Full Analysis** (default) | Comprehensive across all dimensions | All 5 agents | Mixed |
| **Fundamental Analysis** | Financial statements, ratios, earnings, valuation | Fundamental only | Smart (gpt-4o) |
| **Technical Analysis** | Price action, indicators, chart patterns | Technical only | Fast (gpt-4o-mini) |
| **Risk Assessment** | Volatility, VaR, beta, Sharpe, drawdown | Risk only | Smart (gpt-4o) |
| **Sentiment Analysis** | News sentiment, analyst ratings, options activity | Sentiment only | Fast (gpt-4o-mini) |
| **Market Research** | Company overview, SEC filings, competitive landscape | Market Research only | Fast (gpt-4o-mini) |
| **Other (Custom)** | User-defined — custom instructions become the query | Determined by classifier | Mixed |

The backend classifier (LLM-powered) can override these defaults — e.g., a "Full Analysis" query might skip the Technical agent if the classifier determines the user's custom instructions focus only on fundamentals.

## Data Sources & External APIs

### Overview

| Source | Library | API Key Required | Data Provided |
|--------|---------|-----------------|---------------|
| **Yahoo Finance** | `yfinance` | No | Price history, financial statements, analyst ratings, options chains, company info |
| **Technical Indicators** | `ta` (TA-Lib) | No | RSI, MACD, Bollinger Bands, moving averages (computed locally from price data) |
| **Risk Calculations** | `scipy`, `numpy` | No | VaR, beta, Sharpe/Sortino, volatility, drawdown (computed locally from returns) |
| **SEC EDGAR** | `sec-edgar-downloader` | No | 10-K, 10-Q, 8-K filings (full text, public API) |
| **News Articles** | `newsapi-python` | Yes (free tier) | Headlines, descriptions, sources, dates (degrades gracefully without key) |
| **Web Search** | `tavily-python` | Yes (free tier) | Structured web search results + AI-synthesized answer (degrades gracefully) |

### yfinance — Market Data (no API key)

**`get_company_overview(ticker)`** — `yf.Ticker(ticker).info`
- Returns: name, sector, industry, description (500 chars), market cap, employees, country, website, exchange, currency, 52-week high/low, current price, avg volume
- Cache: 1 hour

**`get_price_history(ticker, period="1y", interval="1d")`** — `yf.Ticker(ticker).history()`
- Returns: OHLCV (open, high, low, close, volume) per date
- Periods: 1mo, 3mo, 1y, 5y | Intervals: 1d, 1wk, 1mo
- Cache: 5 minutes

**`get_income_statement(ticker, quarterly=False)`** — `yf.Ticker(ticker).financials`
- Returns: all income statement line items (revenue, COGS, gross profit, operating income, net income, etc.) per period
- Cache: 24 hours

**`get_balance_sheet(ticker, quarterly=False)`** — `yf.Ticker(ticker).balance_sheet`
- Returns: all balance sheet items (total assets, liabilities, equity, cash, debt, etc.) per period
- Cache: 24 hours

**`get_cash_flow(ticker, quarterly=False)`** — `yf.Ticker(ticker).cashflow`
- Returns: all cash flow items (operating CF, investing CF, financing CF, free cash flow, etc.) per period
- Cache: 24 hours

**`get_analyst_ratings(ticker)`** — `yf.Ticker(ticker).info` + `.recommendations`
- Returns: consensus (buy/hold/sell), recommendation mean (1=strong buy, 5=strong sell), price targets (mean/high/low), number of analysts, last 10 upgrades/downgrades with firm name, grade, action
- Cache: 1 hour

**`get_options_chain(ticker)`** — `yf.Ticker(ticker).option_chain(nearest_expiry)`
- Returns: put/call ratio (put open interest / call open interest), total OI for calls and puts, average implied volatility for calls and puts, IV skew (put IV - call IV)
- Interpretation: put/call ratio >1.0 = bearish sentiment
- Cache: 15 minutes

**`get_earnings_calendar(ticker)`** — `yf.Ticker(ticker).calendar` + `.earnings_history`
- Returns: next earnings date, last 8 earnings (EPS estimate, EPS actual, surprise %)
- Cache: 1 hour

### Technical Indicators — `ta` library (computed locally)

All indicators use `yfinance` price data as input and compute values locally using the `ta` Python library. No external API calls.

**`compute_rsi(ticker, period=14)`** — `ta.momentum.RSIIndicator`
- Formula: RSI = 100 - [100 / (1 + RS)], where RS = average gain / average loss over `period` days
- Output: current RSI value (0-100), zone classification:
  - RSI ≥ 70: "overbought" (potential pullback)
  - RSI ≤ 30: "oversold" (potential bounce)
  - 30-70: "neutral"
- Also returns last 20 historical RSI values
- Cache: 5 minutes

**`compute_macd(ticker)`** — `ta.trend.MACD`
- Parameters: fast=12, slow=26, signal=9 (standard)
- Calculation:
  - MACD line = 12-day EMA - 26-day EMA
  - Signal line = 9-day EMA of MACD line
  - Histogram = MACD line - Signal line
- Crossover detection: compares current vs previous histogram sign
  - Bullish crossover: histogram switches from negative to positive
  - Bearish crossover: histogram switches from positive to negative
- Cache: 5 minutes

**`compute_bollinger_bands(ticker, window=20)`** — `ta.volatility.BollingerBands`
- Calculation:
  - Middle band = 20-day SMA
  - Upper band = SMA + 2 × standard deviation
  - Lower band = SMA - 2 × standard deviation
  - %B = (price - lower) / (upper - lower)
  - Bandwidth = (upper - lower) / middle
- Zone classification based on %B:
  - >1.0: above upper band | 0.8-1.0: near upper | 0.2-0.8: middle | 0.0-0.2: near lower | <0.0: below lower
- Cache: 5 minutes

**`compute_moving_averages(ticker)`** — native pandas (not `ta`)
- Computes SMA and EMA for three windows: **20-day, 50-day, 200-day**
- Uses 2 years of price data
- Signal generation:
  - "Price above/below X-day SMA" for each window
  - **Golden cross**: 50-day SMA crosses above 200-day SMA (strong bullish)
  - **Death cross**: 50-day SMA crosses below 200-day SMA (strong bearish)
- Cache: 5 minutes

**`compute_support_resistance(ticker)`** — custom algorithm (not `ta`)
- Algorithm: rolling pivot point method with ±10 bar window
- Uses 1 year of high/low price data
- For each bar: if it's the local maximum within ±10 bars → resistance level; if local minimum → support level
- Returns: 3 nearest resistance levels (above current price), 3 nearest support levels (below)
- Cache: 1 hour

### Financial Ratios — computed from yfinance data

**`compute_financial_ratios(ticker)`** — `yf.Ticker(ticker).info`

All ratios are read directly from yfinance's pre-computed fields, except FCF Yield which is calculated:

| Category | Ratio | yfinance Field | Interpretation |
|----------|-------|---------------|----------------|
| **Valuation** | P/E (TTM) | `trailingPE` | >30 flagged as "high — growth expectations priced in" |
| | P/E (Forward) | `forwardPE` | |
| | Price/Book | `priceToBook` | |
| | Price/Sales | `priceToSalesTrailing12Months` | |
| | EV/EBITDA | `enterpriseToEbitda` | |
| | EV/Revenue | `enterpriseToRevenue` | |
| | PEG Ratio | `pegRatio` | |
| **Profitability** | Gross Margin | `grossMargins` | |
| | Operating Margin | `operatingMargins` | |
| | Net Margin | `profitMargins` | >20% flagged as "high net margin" |
| | ROE | `returnOnEquity` | >15% flagged as "strong ROE" |
| | ROA | `returnOnAssets` | |
| **Leverage** | Current Ratio | `currentRatio` | |
| | Quick Ratio | `quickRatio` | |
| | Debt/Equity | `debtToEquity` | >2.0 flagged as "high leverage" |
| **Growth** | Revenue Growth YoY | `revenueGrowth` | |
| | Earnings Growth YoY | `earningsGrowth` | |
| **Income** | Dividend Yield | `dividendYield` | |
| | EPS (TTM) | `trailingEps` | |
| | EPS (Forward) | `forwardEps` | |
| **Calculated** | FCF Yield | `freeCashflow / marketCap` | Custom calculation |

Cache: 1 hour

### Risk Metrics — scipy + numpy (computed locally)

All risk calculations use daily returns from `yfinance` price history, computed via `close.pct_change()`.

**`compute_historical_volatility(ticker, window=30)`**
- Formula: `rolling_std × √252` (annualized from daily std dev)
- Data: 2 years of daily returns
- Classification: <20% = low, 20-40% = medium, >40% = high
- Cache: 1 hour

**`compute_var(ticker, confidence=0.95)`** — Three methods:

| Method | Formula | Assumption |
|--------|---------|------------|
| **Parametric** | `-(μ + z × σ)` where z = `scipy.stats.norm.ppf(1-α)` | Returns are normally distributed |
| **Historical** | `numpy.percentile(returns, (1-α) × 100)` | No distribution assumption, uses actual return history |
| **Monte Carlo** | Generate 10,000 random normal samples, take percentile | Assumes normal but with simulation variance |

- Holding period: 1 day
- Data: 2 years of daily returns
- Default confidence: 95% (5% worst-case daily loss)
- Cache: 1 hour

**`compute_beta(ticker, benchmark="SPY")`**
- Formula: `Cov(stock, benchmark) / Var(benchmark)`
- Data: 2 years of daily returns, aligned on common trading dates
- Minimum: 20 overlapping data points required
- Interpretation: β < 0.8 = defensive, 0.8-1.2 = market, >1.2 = amplified risk
- Cache: 1 hour

**`compute_sharpe_sortino(ticker)`**
- Risk-free rate: 5% annualized (≈ 0.0198% daily)
- Sharpe: `(mean_excess_return / std_all_returns) × √252`
- Sortino: `(mean_excess_return / std_negative_returns_only) × √252`
- Interpretation: Sharpe >2 = excellent, 1-2 = good, 0-1 = acceptable, <0 = underperforming risk-free
- Cache: 1 hour

**`compute_max_drawdown(ticker)`**
- Formula: `drawdown = (price - running_max) / running_max`
- Data: 5 years of daily close prices
- Returns: worst drawdown %, peak date, trough date, recovery date (or "Not yet recovered")
- Cache: 1 hour

### SEC EDGAR — Filing Downloads (no API key)

**`get_sec_filings(ticker, filing_type="10-K", limit=3)`**
- Library: `sec-edgar-downloader` (wraps the public SEC EDGAR API)
- Registration: company="AgentInvest", email="agent@invest.example.com"
- Process:
  1. Downloads filing documents to a temp directory
  2. Scans for `.txt` and `.htm` files in each filing folder
  3. Reads the primary document file
  4. Extracts first 2000 characters as an excerpt
- Filing types: 10-K (annual report), 10-Q (quarterly), 8-K (current events)
- Returns: accession number, file path, excerpt, SEC browse URL
- Cache: 24 hours
- No API key required — SEC EDGAR is a free public API

### NewsAPI — Headlines & Articles (API key optional)

**`get_recent_news(ticker, days=7, max_results=10)`**
- API: NewsAPI `get_everything()` endpoint
- Query: `'"AAPL" OR "Apple Inc"'` (ticker + company name from yfinance)
- Filters: English, sorted by relevancy, date range from `days` ago
- Returns: title, source name, URL, published date, description (300 chars)
- Graceful degradation: returns empty array + caveat if no API key
- Cache: 15 minutes

### Tavily — Web Search (API key optional)

**`search_web(query, max_results=5)`**
- API: Tavily search (purpose-built for LLM agents)
- Mode: "advanced" search depth
- Returns: Tavily AI-synthesized answer + array of search results (title, URL, content snippet up to 500 chars, relevance score, published date)
- Graceful degradation: returns empty results + caveat if no API key
- Cache: 15 minutes

### Tool Caching Strategy

All tools are cached in-process with tiered TTLs reflecting data freshness needs:

| TTL | Data Type | Tools |
|-----|-----------|-------|
| **5 min** | Real-time price & indicator data | price_history, rsi, macd, bollinger_bands, moving_averages |
| **15 min** | News & search results | recent_news, search_web, options_chain |
| **1 hour** | Relatively stable data | company_overview, analyst_ratings, financial_ratios, all risk metrics, earnings_calendar, support_resistance |
| **24 hours** | Infrequently changing data | income_statement, balance_sheet, cash_flow, sec_filings |

Cache key format: `{tool_name}:{sorted(arguments)}`. Parallel agents analyzing the same ticker share cache hits — if the Fundamental and Risk agents both call `get_price_history("AAPL")`, the second call is a cache hit.

## Agent Responsibilities

### Fundamental Analysis Agent
- **Tools called**: `get_income_statement` (annual + quarterly), `get_balance_sheet`, `get_cash_flow`, `compute_financial_ratios`, `get_earnings_calendar`
- **Model**: Smart tier (gpt-4o) — requires multi-step reasoning to connect ratios to industry context
- **Output**: Financial health narrative, key ratios with interpretations, earnings trends, valuation assessment

### Sentiment Analysis Agent
- **Tools called**: `get_recent_news`, `get_analyst_ratings`, `get_options_chain`, `search_web`
- **Model**: Fast tier (gpt-4o-mini) — pattern classification with few-shot examples
- **Output**: News sentiment score, analyst consensus, put/call ratio interpretation, overall market mood

### Technical Analysis Agent
- **Tools called**: `get_price_history`, `compute_rsi`, `compute_macd`, `compute_bollinger_bands`, `compute_moving_averages`, `compute_support_resistance`
- **Model**: Fast tier (gpt-4o-mini) — interprets computed numbers, no complex reasoning
- **Output**: Trend direction, momentum signals, overbought/oversold zones, key price levels

### Risk Assessment Agent
- **Tools called**: `compute_historical_volatility`, `compute_var`, `compute_beta`, `compute_sharpe_sortino`, `compute_max_drawdown`
- **Model**: Smart tier (gpt-4o) — multi-factor correlation reasoning and tail risk narratives
- **Output**: Risk profile, worst-case scenarios, risk-adjusted return quality, benchmark comparison

### Market Research Agent
- **Tools called**: `get_company_overview`, `get_recent_news`, `get_sec_filings`, `search_web`, `get_earnings_calendar`
- **Model**: Fast tier (gpt-4o-mini) — fact extraction and summarization from retrieved context
- **Output**: Company profile, competitive landscape, recent developments, upcoming catalysts

## RAG (Document Research) Flow

When the user toggles "Include Document Research" ON:

```
1. Check if ticker is already indexed in pgvector
2. If NOT indexed:
   a. Download SEC filings (10-K) via sec-edgar-downloader
   b. Download recent news via NewsAPI
   c. Chunk documents (512 chars, 50 overlap, sentence-aware)
   d. Embed with BAAI/bge-small-en-v1.5 (384 dimensions)
   e. Upsert into PostgreSQL pgvector table
   f. This adds ~15-30s to first analysis for a new ticker
3. If user uploaded files:
   a. Read uploaded TXT/PDF/CSV/MD/HTML files
   b. Chunk + embed + upsert same as above
4. Run 3-stage retrieval:
   a. Query decomposition: LLM breaks query into 3 sub-queries
   b. Multi-vector search: pgvector dense + BM25 sparse + Reciprocal Rank Fusion
   c. Cross-encoder reranking: bge-reranker-base scores top-20 → returns top-8
5. Retrieved context is passed to all agents in their state
```

When toggle is OFF: agents work from live tool data only (yfinance API calls). Faster (~15s) but no document grounding.

## Guardrail Rules

The system enforces these safety rules on every analysis:

### What Gets Flagged
| Rule | Detection Method | Example |
|------|-----------------|---------|
| Direct investment advice | 17 regex patterns | "you should buy", "strong buy", "guaranteed returns" |
| Ungrounded claims | LLM-as-judge vs retrieved docs | Claiming revenue figures not in the data |
| Low confidence | Threshold check | Agent confidence < 0.3 |
| Missing citations | Count check | High confidence but zero source citations |
| No data available | Data point check | Agent returned zero key_data_points |

### What Happens When Flagged
- Flags are stored in `AgentState.guardrail_flags` as structured `GuardrailFlag` objects
- Each flag has: `flag_type`, `agent`, `detail`, `severity` (warning/error)
- Flags are logged to Prometheus (`guardrail_triggers_total`)
- The synthesizer is informed of flags but currently does NOT block synthesis
- The final report always includes a mandatory financial disclaimer

### What Does NOT Happen (Phase 4 planned)
- Flagged text is not automatically rewritten (planned: auto-hedge language)
- High hallucination scores don't lower confidence (planned: recalibration)
- Contradictions between agents aren't flagged (planned: contradiction detection)

## User Memory

### Preferences (PostgreSQL — permanent)
| Field | Values | Effect |
|-------|--------|--------|
| Risk tolerance | conservative / moderate / aggressive | Affects synthesis weighting and language |
| Investment horizon | short / medium / long | Short-horizon users get more technical weight |
| Analysis depth | quick / standard / deep | Controls how many agents run |
| Preferred sectors | list of sectors | Informational — passed to synthesizer |

### Session Memory (Redis — 2hr TTL)
- Recent tickers list (capped at 20, stored per user)
- Session state for multi-request context (planned but not yet wired)

### Analysis History (PostgreSQL — permanent)
- Every completed analysis stored with full results as JSONB
- Queryable by user_id, ticker, status, date
- Feedback (thumbs up/down) linked to analysis runs

## Watchlists

Users can create named watchlists of tickers:
- CRUD via `/api/v1/watchlist` endpoints
- Each watchlist has: name, tickers (array), notes (JSONB)
- "Quick Analyze" button per ticker in the frontend
- Stored in PostgreSQL, survives server restarts

## Feedback Loop

```
User clicks thumbs up/down on analysis
  → POST /api/v1/feedback {run_id, score: 1|-1, comment?}
  → Stored in PostgreSQL feedback table
  → Aggregated at GET /api/v1/feedback/summary
  → Visible in MLflow as a quality signal
```

Currently feedback is stored but not read back into the pipeline. Planned: use feedback to adjust agent behavior for repeat users.

## Financial Disclaimer

Every analysis includes a mandatory disclaimer:

> "This analysis is for informational purposes only and does not constitute financial advice. Always consult a qualified financial advisor before making investment decisions."

- Appended by the synthesizer node regardless of LLM output
- Displayed in the frontend as a yellow `DisclaimerBanner`
- Only shown on user's first visit (persisted in localStorage)
- Cannot be suppressed or removed by the user

## API Rate Limiting

Currently: no rate limiting (development/demo mode).
Planned: Redis token bucket per user_id, configurable rate per endpoint.
