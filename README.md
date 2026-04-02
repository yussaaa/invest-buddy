# Agent Invest

**Multi-Agent Stock Investment Analysis Platform**

A production-grade system that orchestrates 5 specialist AI agents to analyze stocks in parallel — covering fundamentals, technicals, sentiment, risk, and market research — then synthesizes their findings into a comprehensive report with guardrails, citations, and confidence scoring.

## Architecture

```
User Query → [Classifier] → [RAG Prefetch]
                                    │
                    ┌───────────────┼───────────────────┐
                    │               │                   │
             [Market Research] [Sentiment] [Fundamental] [Technical] [Risk]
                    │               │                   │
                    └───────────────┼───────────────────┘
                                    │
                             [Guardrails] → hallucination check, advice detection
                                    │
                             [Synthesizer] → final report with confidence score
                                    │
                              SSE Stream → Frontend
```

**Key design**: All 5 agents run in parallel via LangGraph's `Send` primitive. Total latency ≈ slowest single agent (~15s), not sum of all (~60s).

## Features

- **5 Specialist Agents** — each with domain-specific tools, expert system prompts, and structured JSON outputs
- **22 Financial Tools** — yfinance market data, technical indicators (RSI, MACD, Bollinger), risk metrics (VaR, beta, Sharpe), SEC EDGAR filings, news, web search
- **Multi-Provider LLM Support** — OpenAI (default), Anthropic Claude, Qwen, Ollama (free local), vLLM — swap with one env var
- **MCP Server** — tools exposed as a Model Context Protocol server, connectable from Claude Desktop
- **4-Layer Guardrails** — input validation, tool circuit breakers, LLM-as-judge hallucination detection, investment advice filtering
- **Evaluation Framework** — RAGAS metrics (faithfulness, relevance, precision), golden dataset, MLflow/W&B experiment tracking
- **Live Streaming UI** — SSE-powered agent progress cards, real-time report rendering
- **User Memory** — risk preferences, investment horizon, watchlists, analysis history

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, LangGraph, Pydantic |
| Frontend | React 18, Vite, TypeScript, Tailwind CSS |
| LLM | OpenAI gpt-4o/4o-mini (default) + 4 other providers |
| Data | yfinance, ta, SEC EDGAR, NewsAPI, Tavily |
| Vector Store | Qdrant |
| Database | PostgreSQL, Redis |
| Monitoring | Prometheus, Grafana, MLflow, Weights & Biases |
| Deployment | Docker Compose, Kubernetes (HPA, StatefulSets) |

## Quick Start

```bash
# 1. Clone and set up environment
git clone <repo-url> && cd agent_invest
cp .env.example .env
# Edit .env → add OPENAI_API_KEY=sk-...

# 2. Install dependencies
cd backend && uv venv .venv --python 3.11 && source .venv/bin/activate
uv pip install -e ".[dev]"
cd ../frontend && npm install

# 3. Run locally (no Docker needed)
# Terminal 1:
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# Terminal 2:
cd frontend && npm run dev

# 4. Open http://localhost:5173, type a ticker (e.g. NVDA), hit Analyze
```

### Alternative: Docker Compose

```bash
make dev              # Full stack (requires Docker Desktop)
make dev-ollama       # + Ollama for zero-cost local LLM
make dev-monitoring   # + MLflow + Prometheus + Grafana
```

## Model Provider Tiers

| Tier | Provider | Cost | Best For |
|------|----------|------|----------|
| Free local | Ollama (`qwen2.5:7b`) | $0 | Daily dev, CI tests |
| Cloud API | OpenAI (`gpt-4o-mini` + `gpt-4o`) | ~$0.02/run | Demo, portfolio review |
| Cloud API | Anthropic (Claude Haiku + Sonnet) | ~$0.03/run | Alternative quality |
| Local GPU | vLLM (`Qwen2.5-72B`) | Hardware only | Load testing, production sim |

Switch with one env var: `MODEL_PROVIDER=ollama`

## Testing

```bash
make test       # 18 unit tests — schemas + live yfinance tool calls
make eval       # Offline RAGAS evaluation against golden dataset
make lint       # Ruff linter
```

## Project Structure

```
backend/app/
├── agents/              # 5 specialists + orchestrator + guardrails
│   ├── orchestrator/    # LangGraph StateGraph, classifier, synthesizer
│   ├── fundamental/     # Income statements, ratios, DCF
│   ├── sentiment/       # News sentiment, analyst ratings, options
│   ├── technical/       # RSI, MACD, Bollinger, moving averages
│   ├── risk/            # VaR, beta, Sharpe, drawdown
│   └── market_research/ # Company overview, SEC filings, web search
├── tools/               # 22 tools + MCP server + registry
├── models/              # ModelProvider abstraction (5 providers)
├── api/v1/              # FastAPI routes + SSE streaming
├── rag/                 # 3-stage retrieval pipeline
├── observability/       # MLflow/W&B client, RAGAS evaluator
└── evaluation/          # Golden dataset + eval framework

frontend/src/
├── pages/               # Analyze, Watchlist, History, Settings
├── components/          # Agent tracker, report view, citations
└── hooks/               # useSSE, useAnalysis

infra/
├── docker-compose.yml   # 7 services + 3 optional profiles
└── k8s/                 # Full Kubernetes deployment manifests
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/analysis` | Trigger multi-agent analysis |
| GET | `/api/v1/analysis/{run_id}` | Get completed result |
| GET | `/api/v1/analysis/{run_id}/stream` | SSE live progress events |
| GET | `/api/v1/analysis/history` | Paginated analysis history |
| POST | `/api/v1/feedback` | Submit thumbs up/down |
| CRUD | `/api/v1/watchlist` | Manage watchlists |
| GET/PUT | `/api/v1/preferences` | User memory/preferences |
| GET | `/api/v1/health` | Kubernetes liveness probe |
| GET | `/api/v1/ready` | Kubernetes readiness probe |
| GET | `/metrics` | Prometheus scrape endpoint |

## License

This project is for educational and portfolio demonstration purposes.

---

Built with LangGraph, FastAPI, React, and a lot of financial data.
