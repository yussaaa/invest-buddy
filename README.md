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

## Architectural Decisions

### Vector Database: pgvector over Qdrant

We evaluated five vector database options for the RAG pipeline:

| | pgvector | Qdrant | Pinecone | Weaviate | ChromaDB |
|---|---|---|---|---|---|
| Deployment | PostgreSQL extension | Self-hosted Docker | Cloud-only | Self-hosted | In-process |
| Cost | Free (reuses existing Postgres) | Free (self-hosted) | Paid after free tier | Free (self-hosted) | Free |
| Filtering | SQL WHERE + vector search | Payload filters | Metadata filters | GraphQL filters | Basic |
| Extra infra | None | +1 container + StatefulSet | None (SaaS) | +1 container | None |
| Performance | Good for <1M vectors | Excellent (Rust, HNSW) | Excellent (managed) | Good | Poor at scale |

**Decision: pgvector** for three reasons:

1. **Zero additional infrastructure** — we already run PostgreSQL for persistence. Adding `CREATE EXTENSION vector` is one migration. No extra container in Docker Compose, no extra StatefulSet in K8s, no extra health check in the readiness probe.

2. **Natural SQL filtering** — financial RAG requires filtering by `ticker`, `document_type`, and `published_date` *before* vector search. In pgvector this is a standard `WHERE` clause. In Qdrant this requires payload filters with different syntax. SQL is more expressive and familiar.

3. **Right-sized for the workload** — with ~50 tickers and ~500 chunks per ticker, we're at ~25K vectors. pgvector with HNSW indexing handles this in single-digit milliseconds. A dedicated vector DB becomes necessary at 10M+ vectors — the retrieval layer is abstracted behind a `VectorStore` interface so migrating to Qdrant is a config change, not a rewrite.

**When we'd switch to Qdrant**: If the document corpus grew beyond 1M vectors, or if we needed real-time index updates during high-throughput ingestion, or if vector search latency at p99 exceeded 50ms.

### LLM Provider: OpenAI Default, 5 Providers Swappable

All LLM calls go through a `ModelProvider` abstraction. Swapping providers is one env var (`MODEL_PROVIDER=ollama`). See [Model Provider Tiers](#model-provider-tiers) above.

The cost/latency split (fast model for classification + cheap agents, smart model for reasoning + synthesis) is a deliberate design choice — running Sonnet-tier models for every agent call would cost ~5x more with marginal quality improvement on data extraction tasks.

### LangGraph over Plain LangChain

LangGraph gives explicit state management, conditional edges, and streaming checkpoints. This is essential for a multi-agent system where you need to:
- Inspect partial results mid-pipeline
- Retry individual agents without rerunning the entire graph
- Stream per-agent progress events to the frontend via SSE

Plain LangChain `AgentExecutor` is a black box that doesn't support any of these.

### Structured Outputs Everywhere

Every LLM call returns a Pydantic-validated `AgentResult`, not free-form text. This:
- Prevents prompt injection from propagating through the pipeline
- Makes evaluation programmatic (compare JSON fields, not prose)
- Forces the LLM to produce citable, auditable outputs
- Enables the guardrails node to check specific fields (confidence, citations)

## License

This project is for educational and portfolio demonstration purposes.

---

Built with LangGraph, FastAPI, React, and a lot of financial data.
