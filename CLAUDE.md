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

The whole stack runs in containers — this is the intended path, and the only one
that gives you pgvector without installing it locally:

```bash
cd infra && docker compose up -d     # API, frontend, Postgres, Redis
```

Migrations run as their own service before the API starts. Host ports are
overridable — `BACKEND_PORT`, `FRONTEND_PORT`, `POSTGRES_PORT`, `REDIS_PORT` —
so a local Postgres or Redis on the default port doesn't block the stack.

Running natively instead needs Postgres with the `vector` extension available;
plain `postgresql@15` from Homebrew does not have it, and the app will fail at
startup with `extension "vector" is not available`.

```bash
# Native, if you have pgvector locally
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev

# Tests
make test          # unit tests only — no DB, no network
make test-all      # adds integration tests, needs a real PostgreSQL
make migrate       # safe on a schema previously built by dev create_all
```

## Parallel work with git worktrees

Several features have been in flight at once, and sharing one working tree meant
three of them editing `services/technicals.py` simultaneously with nothing
committed. Worktrees give each branch its own directory:

```bash
git worktree add ../agent_invest-<name> -b <branch> dev
```

**Branch each feature off `dev`, not off the previous feature.** This is the
part that is easy to get wrong, because the mistake is invisible until it bites.
Phases 10 through 13 were each cut from the tip of the one before, which made
them a stack rather than four parallel tracks:

```
dev ── phase-10 ── phase-11 ── phase-12 ── phase-13     # what happened
                                                        # (each contains all the earlier ones)

dev ─┬─ phase-10                                        # what was wanted
     ├─ phase-11
     ├─ phase-12
     └─ phase-13
```

Four symptoms of a stack, all of which showed up:

- The Docker stack bind-mounts the *main* worktree, so whichever branch is
  checked out there serves every feature below it too. "Why can I see the
  options panel and the drawdown panel and the sidebar work?" — because the tip
  contains all three, not because worktrees leak.
- A fix on an upstream branch does not reach the branches cut from it. It has to
  be rebased forward, one branch at a time, in order.
- Every branch inherits the others' bugs, so a bisect points at the wrong
  feature.
- Nothing can merge to `dev` independently. Merging phase-12 drags phases 10
  and 11 in with it, whether or not they are ready.

If a feature genuinely needs another one's code, cut it from that branch on
purpose and write down why. Inheriting by accident is the thing to avoid.

To straighten a stack out afterwards — safe while the branches are local, and
worth checking with `git branch -r` first, since rewriting a pushed branch needs
a force-push:

```bash
git update-ref refs/backup/<name> <branch>          # reflog is not forever
git rebase --onto dev <old-base> <branch>
```

Each worktree needs the gitignored pieces bridged. Symlink rather than copy —
`.venv` is 1.3 GB and `node_modules` 159 MB, so copying costs gigabytes while a
symlinked worktree costs about 1.4 MB:

```bash
MAIN=/Users/yusali/dev/agent_invest
ln -sfn "$MAIN/backend/.venv"         <worktree>/backend/.venv
ln -sfn "$MAIN/frontend/node_modules" <worktree>/frontend/node_modules
ln -sf  "$MAIN/.env"                  <worktree>/.env
```

Three things to know:

- **The backend is installed editable**, so a worktree could in principle import
  `app` from the main tree and silently test the wrong code. It doesn't — CWD
  wins — but it is worth re-checking if imports ever behave strangely:
  `python -c "import app.services.market_data as m; print(m.__file__)"` should
  print the worktree's own path.
- **Only one worktree can drive the Docker stack on the default ports**, because
  `infra/docker-compose.yml` bind-mounts `../backend/app` and `../frontend`
  relative to itself. Verify UI work in whichever tree the stack is up from, or
  start a second one with the port overrides above.
- **The symlinked `.venv` and `node_modules` are shared**, so a branch that adds
  a dependency changes them for every worktree. If two branches need different
  versions, give that worktree a real directory instead of the symlink.

Remove with `git worktree remove <path>` — deleting the directory by hand leaves
a stale registration that needs `git worktree prune`.

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

### Phase 5 — RAGAS Evaluation + MLflow/W&B ✅ COMPLETE
- Online RAGAS scoring in persistence_node (faithfulness, relevancy, precision)
- MLflow v3.x experiment tracking: every run logged with params, metrics, prompt versions
- 14 Prometheus custom metrics (RAGAS, latency, tools, guardrails, tokens, cost)
- Grafana dashboard: 5 rows, 14 panels, auto-provisioned
- Token usage + cost estimation per LLM call (~$0.011/run with gpt-4o)
- Prompt versioning (PROMPT_VERSION=v1 in all 5 agents)

### Phase 6 — Kubernetes + Monitoring ✅ COMPLETE
- 14 K8s manifests: namespace, backend, frontend, postgres, redis, ingress, monitoring
- Deployed to minikube: 9/9 pods running (backend×2, frontend×2, postgres, redis, prometheus, grafana, mlflow)
- HPA: 2-5 backend replicas, CPU target 70%
- Frontend: multi-stage Dockerfile (Node build → nginx serve) + nginx proxy for /api + SSE
- Monitoring: Prometheus scraping backend, Grafana with provisioned dashboard, MLflow v3.1.0
- Removed Qdrant (replaced by pgvector inside Postgres)
- Fixed: Postgres image → pgvector/pgvector:pg16, MLflow port → 5050, readiness probe cleaned
- Load testing with locust

### Phase 7 — Documentation + Demo
- Architecture diagrams (Mermaid or draw.io)
- docs/model_selection.md — formal write-up of model tradeoff POV
- docs/evaluation_framework.md — methodology document
- Demo video showing streaming multi-agent analysis
- README with screenshots

### Phase 8 — Market Data + Pricing Dashboard ✅ COMPLETE
- Charting tab: TradingView Lightweight Charts v5 — candles/line/area, volume pane,
  MA20/50/200 overlays, crosshair OHLC legend, 9 timeframes (1D intraday → MAX)
- Market tab: index cards with sparklines, TradingView live heatmap embed
  (index + performance-window pickers), sector performance, rates/commodities/crypto
- Advance/decline breadth per index, computed over real constituent lists
- Week-ahead panel: earnings as a trading-day calendar, economic releases (optional FRED_API_KEY)
- Technical analysis section: RSI, MACD, 5/20/50/250 MA ladder + on-demand LLM explanation
- TradingView-style right-hand watchlist rail: collapsible sections, drag-to-resize,
  per-page collapse memory
- `GET /api/v1/market/*` — quotes, history, profile, overview, breadth, events, technicals

**Deviations from the original plan, and why:**
- **No WebSocket feed.** yfinance has no streaming API and gives delayed data. The UI
  polls (quotes 15s) instead. A real-time feed needs a paid provider (Polygon/Alpaca/
  Finnhub) — that is the natural upgrade, and `useQuotes` is the single place to change.
- **Heatmap is TradingView's embed**, not our own treemap. Their widget is genuinely
  real-time; ours was delayed. Trade-off: tiles link out to TradingView rather than our
  Charting page.
- **No bid/ask** — not available from the data source.

### Phase 9 — Market Data Caching + Local Price Store ✅ COMPLETE
- Redis as a shared L2 behind the in-process cache (`app/services/cache.py`), with
  single-flight, stale-fallback, and a circuit breaker so Redis being down degrades
  to L1 rather than failing
- `daily_bars` + `bar_coverage` tables — a store, not a cache: settled EOD bars never
  expire, so a 5-year indicator window is one query. Stores raw *and* adjusted closes
  because charts want raw and indicators want adjusted
- `bar_store` serves from Postgres, falls back to the provider, and never serves
  today's in-progress session
- Technicals cut over: three downloads per page load → one ~10ms query
- Full stack containerized; migrations run as an explicit step via `python -m app.db.migrate`,
  which is safe on a schema previously built by dev `create_all`

**Still open:** `get_history`/`get_overview`/`get_breadth` still fetch live; no backfill
job yet (the store warms lazily); `tests/unit/test_tools.py` still makes live network calls.

## Common Issues

- **API key not loading**: `.env` must be at project root, not `backend/.env`
- **pandas-ta not available**: We use `ta` library instead (Python 3.11 compatible)
- **Makefile errors**: Recipes must use hard tabs, not spaces
- **Docker not running**: `make dev-backend` + `make dev-frontend` run natively, but the
  backend still needs Postgres with pgvector — see How to Run
- **`extension "vector" is not available`**: the database has no pgvector. Use the
  containerized stack, or `brew install pgvector` for a native one
- **`alembic upgrade head` says "relation users already exists"**: the schema was built by
  dev `create_all`, which records no revision. Use `make migrate` (`python -m app.db.migrate`),
  which stamps an unstamped schema to the right revision before upgrading
