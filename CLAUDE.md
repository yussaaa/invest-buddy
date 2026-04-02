# CLAUDE.md — Project Context for Claude Code

## What is this project?

Agent Invest is a multi-agent stock investment analysis platform. It uses LangGraph to orchestrate 5 specialist AI agents (Fundamental, Sentiment, Technical, Risk, Market Research) that run in parallel, each calling domain-specific tools, producing structured outputs that are checked by guardrails and synthesized into a final report.

This is a **portfolio project** for Senior AI Engineer roles — designed to demonstrate production-grade agent orchestration, RAG, evaluation, and deployment skills.

## Tech Stack

- **Backend**: Python 3.11, FastAPI, LangGraph, Pydantic
- **Frontend**: React 18, Vite, TypeScript, Tailwind CSS
- **LLM Providers**: OpenAI (default), Anthropic, Qwen, Ollama, vLLM — swappable via `MODEL_PROVIDER` env var
- **Data**: yfinance (market data), ta (technical indicators), NewsAPI, SEC EDGAR, Tavily (web search)
- **Infrastructure**: Docker Compose, Kubernetes, PostgreSQL, Redis, Qdrant
- **Monitoring**: Prometheus, Grafana, MLflow, W&B, RAGAS

## Project Structure

```
backend/app/
  agents/           # 5 specialist agents + orchestrator + guardrails
  tools/            # 22 tools (yfinance, ta, risk, news, web) + MCP server + registry
  models/           # ModelProvider abstraction (OpenAI, Anthropic, Qwen, Ollama, vLLM)
  api/v1/           # FastAPI routes (analysis, watchlist, preferences, feedback, health)
  rag/              # RAG pipeline (Phase 3 — currently stubbed)
  observability/    # MonitoringClient (MLflow/W&B), RAGAS evaluator
  evaluation/       # Golden dataset + offline eval framework
frontend/src/
  pages/            # AnalyzePage, WatchlistPage, HistoryPage, SettingsPage
  components/       # AgentStatusTracker, FinalReportView, SourceCitations
  hooks/            # useSSE, useAnalysis
infra/
  docker-compose.yml
  k8s/              # Kubernetes manifests
```

## Key Architecture Decisions

1. **LangGraph StateGraph** with parallel fan-out via `Send` — all 5 agents run concurrently, not sequentially
2. **Structured outputs everywhere** — every agent returns a validated Pydantic `AgentResult`, not free-form text
3. **MCP-style tool registry** — tools are declarative and centrally registered; adding a new data source is one file + one registry call
4. **ModelProvider abstraction** — swap from OpenAI to Ollama (free local) with one env var change
5. **Guardrails as a graph node** — hallucination detection (LLM-as-judge), investment advice detection (regex), citation checks run between agents and synthesis

## How to Run

```bash
# Backend
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend && npm run dev

# Tests
make test          # 18 unit tests (schemas + tools)
```

## Environment Variables

The `.env` file must be at the **project root** (not inside `backend/`). `config.py` walks up the directory tree to find it. Key vars:
- `MODEL_PROVIDER` — openai | anthropic | qwen | ollama | vllm
- `OPENAI_API_KEY` — required for default provider
- `NEWSAPI_KEY`, `TAVILY_API_KEY` — optional, tools degrade gracefully without them

## Important Files

- `backend/app/agents/orchestrator/graph.py` — LangGraph StateGraph (the architectural centerpiece)
- `backend/app/agents/base/state.py` — AgentState TypedDict shared across all nodes
- `backend/app/agents/base/schemas.py` — AgentResult, FinalReport, QueryClassification
- `backend/app/tools/registry.py` — Central tool registry (22 tools)
- `backend/app/models/provider.py` — ModelProvider abstraction
- `backend/app/config.py` — Settings with auto-discovery of .env file

## Testing

```bash
make test       # Unit tests — schemas + tools (uses real yfinance, no mocks)
make test-all   # All tests including integration
make eval       # RAGAS evaluation against golden dataset
```

## Implementation Phases

### Phase 1 — Core Engine + Single-agent Loop ✅ COMPLETE
- Project scaffold, config, ModelProvider abstraction (5 providers)
- All 22 tools (yfinance, ta, risk metrics, SEC EDGAR, NewsAPI, Tavily)
- All 5 specialist agents with prompts and structured outputs
- LangGraph orchestrator with parallel fan-out via Send
- Guardrails node (hallucination detection, investment advice check)
- Synthesizer node (chain-of-thought merge)
- FastAPI routes with SSE streaming
- React + Vite frontend with live agent tracker
- In-memory storage (watchlists, preferences, history)
- Docker Compose + K8s manifests
- 18 unit tests passing, frontend builds clean

### Phase 2 — Persistence + Production Polish (next)
- PostgreSQL persistence for analysis_history, user_preferences, watchlists
- Alembic migrations
- Redis-backed session memory (conversation history, recent tickers)
- Replace in-memory stores with DB queries
- Proper error handling and retry logic on API routes
- Integration tests against real analysis runs

### Phase 3 — RAG Pipeline
- Qdrant setup with financial_documents collection
- Document ingestion: SEC filings + news → chunked → embedded → upserted
- Query decomposition (Haiku breaks query into sub-queries)
- Multi-vector retrieval: dense (Qdrant) + sparse (BM25) + entity graph
- Cross-encoder reranking (bge-reranker-base)
- Context assembly with freshness weighting
- Wire RAG prefetch node in the LangGraph (currently stubbed)

### Phase 4 — Memory + Guardrails Enhancement
- Long-term user profile learning (preferred sectors, risk drift)
- Conversation memory across sessions
- Guardrails v2: citation completeness enforcement, confidence recalibration
- Investment advice rewriting (not just detection — auto-hedge language)

### Phase 5 — RAGAS Evaluation + MLflow/W&B
- Expand golden dataset to 50 queries
- Wire RAGAS scoring into every production run (online eval)
- MLflow experiment tracking: log every analysis run with params, metrics, artifacts
- Prompt versioning via MLflow Model Registry
- W&B Weave integration for LLM call tracing
- Grafana dashboard: RAGAS trends, token costs, latency P50/P95

### Phase 6 — Kubernetes + Monitoring
- Deploy full stack to minikube/GKE
- HPA autoscaling verified under load
- Prometheus metrics: token usage, cache hit rate, guardrail trigger rate
- Grafana dashboards (system health, quality metrics, business metrics)
- Load testing with locust

### Phase 7 — Documentation + Demo
- Architecture diagrams (Mermaid or draw.io)
- docs/model_selection.md — formal write-up of model tradeoff POV
- docs/evaluation_framework.md — methodology document
- Demo video showing streaming multi-agent analysis
- README with screenshots

## Common Issues

- **API key not loading**: `.env` must be at project root, not `backend/.env`
- **pandas-ta not available**: We use `ta` library instead (Python 3.11 compatible)
- **Makefile errors**: Recipes must use hard tabs, not spaces
- **Docker not running**: Use `make dev-backend` + `make dev-frontend` for local dev without Docker
