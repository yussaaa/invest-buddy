# CLAUDE.md — Project Context for Claude Code

## What is this project?

Agent Invest is a multi-agent stock investment analysis platform. It uses LangGraph to orchestrate 6 specialist AI agents (Fundamental, Sentiment, Technical, Risk, Market Research, Options) that fan out in parallel, each calling domain-specific tools, producing structured outputs that are checked by guardrails and synthesized into a final report.

This is a **portfolio project** for Senior AI Engineer roles — designed to demonstrate production-grade agent orchestration, RAG, evaluation, and deployment skills.

## Where the documentation is

This file is the orientation: enough to start work without reading everything.
The detail lives elsewhere.

| Read | For |
|---|---|
| **[docs/architecture.md](docs/architecture.md)** | The full reference — every subsystem, the three request flows, why the awkward bits are that way. **Start here when picking this up cold.** |
| **[docs/roadmap.md](docs/roadmap.md)** | What is built and what is not, verified against code. Authoritative over any phase list. |
| [docs/business_logic.md](docs/business_logic.md) | Data sources and domain rules |
| [docs/model_selection.md](docs/model_selection.md) | The model tradeoff write-up |
| [docs/evaluation_framework.md](docs/evaluation_framework.md) | Evaluation methodology |
| [README.md](README.md) | Public-facing overview and quick start |

## Tech Stack

- **Backend**: Python 3.11, FastAPI, LangGraph, Pydantic
- **Frontend**: React 18, Vite, TypeScript, Tailwind CSS
- **LLM Providers**: OpenAI (default), Anthropic, Qwen, Ollama, vLLM — swappable via `MODEL_PROVIDER` env var
- **Data**: yfinance (market data), ta (technical indicators), NewsAPI, SEC EDGAR, Tavily (web search)
- **Infrastructure**: Docker Compose, Kubernetes, PostgreSQL (+pgvector), Redis
- **Monitoring**: Prometheus, Grafana, MLflow, W&B, RAGAS

## Project Structure

```
backend/app/
  agents/           # 6 specialists + orchestrator + chat agent + guardrails
    orchestrator/   #   graph.py (the centrepiece), router.py, synthesiser.py
    chat/           #   bounded ReAct cycle behind the floating agent
    base/           #   AgentState, AgentResult schemas, guardrails
  services/         # the non-LLM half: market data, options, technicals,
                    # signals, cache, bar store, drawdown, volatility
  tools/            # 24 tools + MCP server + registry
  models/           # ModelProvider abstraction (5 providers, fast/smart tiers)
  rag/              # ingestion + 3-stage retrieval over pgvector (built)
  api/v1/           # 8 routers: analysis chat market watchlist
                    # preferences feedback upload health
  db/               # SQLAlchemy models, Alembic, repositories (9 tables)
  memory/           # Redis-backed session + chat history
  cache/            # Redis client
  observability/    # MLflow/W&B client, RAGAS evaluator, Prometheus metrics
  evaluation/       # Golden dataset + offline eval
frontend/src/
  pages/            # Charting, Market, Analyze, Watchlist, History, Settings
  components/       # analysis/ chart/ chat/ market/ ui/
  hooks/            # useSSE, useAnalysis, useChatStream, useQuotes
  context/          # ScreenContext — tells the chat agent what is on screen
  lib/              # api.ts (all URLs), types.ts, sse.ts
infra/
  docker-compose.yml
  k8s/              # 14 Kubernetes manifests
docs/
  architecture.md   # the detailed reference — start here
  roadmap.md        # authoritative status and open work
  business_logic.md evaluation_framework.md model_selection.md
```

## Key Architecture Decisions

1. **LangGraph StateGraph** with parallel fan-out via `Send` — the specialists run
   concurrently, so total latency is the slowest agent rather than the sum. The
   fan-out is conditional: `orchestrator/router.py` picks which specialists a
   query actually needs
2. **Structured outputs everywhere** — every agent returns a validated Pydantic `AgentResult`, not free-form text. This is what makes a guardrails node possible: it checks fields, not prose
3. **MCP-style tool registry** — tools are declarative and centrally registered; adding a new data source is one file + one registry call
4. **ModelProvider abstraction** — swap from OpenAI to Ollama (free local) with one env var change. Every provider exposes a fast and a smart tier, so cheap and expensive calls are a config choice
5. **Guardrails as a graph node** — hallucination detection (LLM-as-judge), investment advice detection (regex), numeric grounding (arithmetic, not judgement), citation checks
6. **The reasoning that must not hallucinate is Python, not a prompt** — `services/signals.py` derives every technical condition deterministically, and contains no action verb by construction. The guardrail can then be honest instead of something to work around
7. **Two-tier cache in front of a real bar store** — settled EOD bars never expire, so indicator windows are one query rather than a download; L1+L2 with single-flight and stale-fallback covers everything live

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
- `backend/app/tools/registry.py` — Central tool registry (24 tools)
- `backend/app/models/provider.py` — ModelProvider abstraction
- `backend/app/config.py` — Settings with auto-discovery of .env file
- `backend/app/agents/chat/graph.py` — the chat agent's bounded ReAct cycle
- `backend/app/services/signals.py` — deterministic buy/sell reasoning in Python,
  with no action verb anywhere in the module by construction
- `backend/app/services/cache.py` — L1+L2 with single-flight and stale-fallback;
  every market read goes through it
- `backend/app/services/trend.py` — SMA slope and z-score vs the 200 DMA; note
  why its series must never enter the technicals payload
- `backend/app/services/valuation_math.py` — DCF arithmetic, assumptions in the
  signature; the reverse DCF is the number worth leading with
- `frontend/src/lib/clientCache.ts` — the client-side twin of services/cache.py
- `backend/app/services/market_data.py` — quotes, overview, breadth, movers, events
- `backend/app/rag/retrieval/pipeline.py` — 3-stage retrieval (decompose,
  dense+BM25+RRF, cross-encoder rerank)
- `frontend/src/lib/api.ts` — the only place that knows backend URLs

## Testing

```bash
make test       # Unit tests — schemas + tools (uses real yfinance, no mocks)
make test-all   # All tests including integration
make eval       # RAGAS evaluation against golden dataset
```

## Implementation Phases

Sixteen phases are shipped. **[docs/roadmap.md](docs/roadmap.md) is the
authoritative status** — every line there was checked against the code rather
than carried forward from the previous plan. This table is the summary.

| # | Phase | Status |
|---|---|---|
| 1 | Core engine — 6 agents, LangGraph fan-out, guardrails, synthesiser | ✅ |
| 2 | Persistence — Postgres, Alembic, Redis session memory | ✅ |
| 3 | RAG — ingestion, dense + BM25 + RRF, cross-encoder rerank | ✅ |
| 4 | Memory + guardrails v2 | ❌ not started |
| 5 | RAGAS evaluation + MLflow / Prometheus / Grafana | ✅ |
| 6 | Kubernetes + monitoring | ✅ |
| 7 | Documentation + demo | ◐ docs written, demo video and screenshots outstanding |
| 8 | Market data + pricing dashboard | ✅ |
| 9 | Market data caching + local bar store | ✅ |
| 10 | UI polish — collapsible sidebar, persisted chart toolbar | ✅ |
| 11 | Options — math, screener, vol provider, specialist agent | ✅ |
| 12 | Drawdown panel — distance below the 52-week high | ✅ |
| 13 | Conversational agent — bounded ReAct over deterministic signals | ✅ |
| 14 | Top movers by cap tier + navigable events week | ✅ |
| 15 | Client-side cache — instant tab switches, persisted toolbars | ✅ |
| 16 | Trend analytics + reverse-DCF valuation panel | ✅ |

Phase 4 is the only wholly unstarted one. The open items inside shipped
phases — a lazily-warmed bar store, live network calls in the unit suite, a
36s cold earnings-calendar fetch — are all listed in the roadmap.

> **A warning about this list.** It previously described Phase 3 as "currently
> stubbed" while 1,063 lines of working RAG sat in `app/rag/`, and omitted
> phases 10–12 entirely. If a status here disagrees with the code, trust the
> code and fix this table.


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
- **`docker compose` fails with a socket error**: Docker Desktop itself is not
  running. `open -a Docker`, wait for the daemon, retry
- **A cached `None` reads back as a cache miss**: store a sentinel (`""`) if you
  need to cache a negative result, or it re-fetches on every poll
- **Times must be anchored to America/New_York, not UTC**: after 8pm ET the UTC
  date has rolled over, which drops today's events off the calendar and, on a
  Sunday night, returns the wrong week
- **Yahoo and GICS disagree on four sector names**: normalise through
  `market_data.normalise_sector` before showing them in one list
- **Trust the code over any status list here**: a stale "stub for Phase 1"
  comment in `orchestrator/graph.py` kept RAG marked unbuilt for months while
  it was fully working. Fixed, but the lesson stands
