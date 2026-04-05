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

## Agent Responsibilities

### Fundamental Analysis Agent
- **Data sources**: yfinance (income statement, balance sheet, cash flow, financial ratios, earnings calendar)
- **Output**: P/E, P/B, EV/EBITDA, ROE, ROA, debt/equity, FCF yield, revenue growth trends
- **Model**: Smart tier — requires multi-step reasoning to connect ratios to industry context

### Sentiment Analysis Agent
- **Data sources**: NewsAPI (headlines), yfinance (analyst ratings, options chain), Tavily (web search)
- **Output**: News sentiment score, analyst consensus (buy/hold/sell), put/call ratio, IV skew
- **Model**: Fast tier — pattern classification with few-shot examples

### Technical Analysis Agent
- **Data sources**: yfinance (price history), `ta` library (RSI, MACD, Bollinger Bands, moving averages)
- **Output**: RSI zone (overbought/oversold/neutral), MACD crossovers, Bollinger %B, support/resistance levels, golden/death cross detection
- **Model**: Fast tier — interprets computed numbers, no complex reasoning

### Risk Assessment Agent
- **Data sources**: yfinance (price history), scipy (VaR), numpy (Monte Carlo)
- **Output**: Annualized volatility, VaR (parametric + historical + Monte Carlo), beta vs SPY, Sharpe/Sortino ratios, max drawdown with recovery period
- **Model**: Smart tier — multi-factor correlation reasoning and tail risk narratives

### Market Research Agent
- **Data sources**: yfinance (company overview), SEC EDGAR (10-K, 10-Q, 8-K filings), NewsAPI, Tavily
- **Output**: Company profile, recent news summary, SEC filing excerpts, competitive landscape, upcoming earnings
- **Model**: Fast tier — fact extraction and summarization from retrieved context

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
