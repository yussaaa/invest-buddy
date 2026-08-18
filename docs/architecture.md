# Architecture

A working reference for the whole system: what each part does, how a request
moves through it, and why the awkward bits are the way they are.

`CLAUDE.md` is the short orientation file. This is the long one. For what is
built versus what is not, see [roadmap.md](roadmap.md).

---

## 1. The shape of the thing

Three independent surfaces share one backend. They are worth separating in your
head, because they have almost nothing in common at runtime:

| Surface | Entry point | Cost | Latency |
|---|---|---|---|
| **Analysis** — the multi-agent report | `POST /api/v1/analysis` | ~$0.011/run | 30–60s |
| **Chat** — the floating agent | `POST /api/v1/chat[/stream]` | ~2 model calls | 8–9s cold |
| **Market dashboard** — charts, quotes, movers | `GET /api/v1/market/*` | no model calls | 10ms–40s |

The dashboard is the one people actually click around in, and it never invokes
an LLM except on explicit "explain this" buttons. Keep that distinction: most
of the market code is plain HTTP reads with aggressive caching, and it should
never grow a model call on a hot path.

```
                        ┌──────────────────────────────┐
   React (Vite, 5173)   │  /charting  /market  /analyze │
                        │  /watchlist /history /settings│
                        └───────────────┬──────────────┘
                                        │ REST + SSE
                        ┌───────────────▼──────────────┐
   FastAPI (8000)       │  api/v1/router.py            │
                        │  analysis chat market        │
                        │  watchlist preferences       │
                        │  feedback upload health      │
                        └───┬──────────┬────────────┬──┘
                            │          │            │
              ┌─────────────▼──┐  ┌────▼──────┐  ┌──▼────────────┐
              │ orchestrator   │  │ chat      │  │ services/     │
              │ (LangGraph)    │  │ (LangGraph│  │ market_data   │
              │ 6 agents       │  │  cycle)   │  │ technicals    │
              └───┬────────┬───┘  └────┬──────┘  │ options       │
                  │        │           │         │ signals       │
            ┌─────▼───┐ ┌──▼──────┐ ┌──▼──────┐  └──┬────────────┘
            │ tools/  │ │ rag/    │ │ signals │     │
            │ (24)    │ │pgvector │ │ (pure)  │     │
            └────┬────┘ └────┬────┘ └─────────┘     │
                 │           │                       │
            ┌────▼───────────▼───────────────────────▼────┐
            │  Postgres (+pgvector)   Redis   yfinance    │
            └─────────────────────────────────────────────┘
```

---

## 2. Flow one — the analysis pipeline

`backend/app/agents/orchestrator/graph.py` is the architectural centrepiece.
A LangGraph `StateGraph` over `AgentState` (`agents/base/state.py`):

```
classifier_node
     ↓
rag_prefetch_node
     ↓
[conditional fan-out via Send]
     ↓
market_research | sentiment | fundamental | technical | risk | options
     ↓                    (all concurrent)
guardrails_node
     ↓
synthesiser_node
     ↓
persistence_node
     ↓
   END
```

**The fan-out is the point.** LangGraph's `Send` dispatches each agent as an
independent node invocation sharing one `AgentState`, with reducer annotations
so concurrent writes are safe. Total latency is the slowest single agent, not
the sum. Running these sequentially would be 5× slower for identical output.

**The fan-out is conditional, not fixed.** `orchestrator/router.py` decides
which specialists a given query needs — an options question does not need the
sentiment agent. The options agent in particular is opt-in; it was added in
Phase 11 and joins the fan-out only when the query calls for it.

**Every agent returns a validated Pydantic `AgentResult`**, never free text
(`agents/base/schemas.py`). This is what makes the guardrails node possible: it
has structured fields to check rather than prose to parse. Each agent lives in
`agents/<name>/` as `agent.py` + `prompts.py`, and every prompt carries a
`PROMPT_VERSION` so a regression can be traced to a wording change.

### The guardrails node

`agents/base/guardrails.py`, run between the agents and synthesis:

| Check | Method | What it catches |
|---|---|---|
| `check_hallucination` | LLM-as-judge | Claims unsupported by retrieved context |
| `check_investment_advice` | regex, two modes | "You should buy" phrasing |
| `check_numeric_grounding` | arithmetic | Figures not present in the evidence |
| `inject_disclaimer` | append | Regulatory footer |

`check_investment_advice(text, mode)` takes `"strict"` or `"chat"`. The chat
tier is a strict superset — it adds capitulation patterns, because a
conversation gets argued with in ways a written report never is.

`check_numeric_grounding` is deliberately arithmetic rather than judgement: it
collects every number in the reply and every number in the evidence shown to
the model, and flags any figure that appears in the first set but not the
second. A model that invents a P/E ratio gets caught by comparison, not by
another model's opinion.

---

## 3. Flow two — the chat agent

`backend/app/agents/chat/graph.py`. A different graph shape on purpose:

```
prepare_context → gather ⇄ tools → answer → verify
                   (bounded cycle, cap 3)
```

Where the analysis graph is a one-shot parallel fan-out, this is a bounded
ReAct cycle closed by a deterministic node. Four things about it are load-
bearing:

**Context is fetched before the first model call.** `prepare_context_node`
pulls technicals, quote, profile and events for whatever ticker is on screen,
so even a zero-tool turn is fully grounded. The agent never has to call a tool
just to know what it is looking at.

**Gather is a separate model call from answer.** It costs one cheap call and
buys a clean streaming path: by the time `answer_node` starts emitting tokens,
no tool request can arrive mid-sentence.

**The reasoning is Python, not a prompt.** `services/signals.py` is the
deterministic half. `derive_signals()` is pure over a `get_technicals()`
payload and names every condition that holds — RSI extremes, MACD crossovers,
the MA ladder, 50/200 crosses, extension from the 50-day, position in the
52-week range, upcoming earnings. Each signal carries evidence, the horizon it
speaks to, a reliability grade, and its invalidation condition.

> There is **no action verb anywhere in `signals.py`, by construction.**
> Direction is `bullish` / `bearish` / `neutral`, never "buy". This is what
> makes the investment-advice guardrail coherent rather than something to work
> around.

**Failed guardrails rebuild the reply, they do not refuse it.** `verify_node`
replaces a bad answer with one reconstructed from the signal set.

Streaming walks the node functions directly rather than calling `ainvoke`,
because token streaming has to reach inside the answer step and a compiled
graph does not expose that. History lives in Redis (`memory/session.py`), not
a LangGraph checkpointer — only `InMemorySaver` ships with the installed
langgraph, and it survives neither a restart nor a second uvicorn worker.

---

## 4. Flow three — the market dashboard

No LLM. `api/v1/market.py` is deliberately thin; everything lives in
`services/market_data.py`, `technicals.py` and `options.py`.

The read path is layered, fastest first:

```
request → L1 in-process TTL cache
        → L2 Redis (shared across workers/replicas)
        → Postgres daily_bars      (settled EOD bars, never expire)
        → yfinance                 (live, slow, rate-limited)
```

`services/cache.py` implements L1+L2 with **single-flight** (one fetch per key
across replicas), **stale-fallback** (serve the last good value rather than an
error), and a **circuit breaker** so Redis being down degrades to L1 instead of
failing the request.

`should_cache` is the guard worth knowing: yfinance drops symbols from a batch
now and then, and pinning a partial response would show gaps for the whole TTL
instead of self-healing on the next poll.

### The bar store is a store, not a cache

`daily_bars` + `bar_coverage` (`services/bar_store.py`). Settled end-of-day
bars never change, so they never expire — a 5-year indicator window is one
query rather than a download. It stores **raw and adjusted closes both**,
because charts want raw and indicators want adjusted. It never serves today's
in-progress session. This took technicals from three downloads per page load
to one ~10ms query.

### TTLs, and why each is what it is

| Data | TTL | Reason |
|---|---|---|
| quotes | 10s | Delayed feed anyway; UI polls 15s |
| history / overview | 30s | Chart redraws should feel live |
| breadth | 60s | ~700-symbol constituent scan |
| movers | 60s | One screener call per side |
| events (forward) | 30 min | Earnings dates barely move |
| earnings calendar | 6 h | ~36s to fetch; sliced per week after |
| sectors | 7 days | A company changes sector roughly never |
| index constituents | 24 h | Membership changes a few times a year |
| profile | 10 min | Static-ish company metadata |

### Top movers (Phase 14)

`GET /market/movers?cap=all|large|mid|small`. Uses Yahoo's own equity screener
rather than scanning our universe: ~1.5s per side against the whole US tape,
versus ~95s to price the S&P 1500 ourselves — and it reaches small caps that no
membership list covers.

The screener returns no sector, so that arrives in two layers: the S&P 1500
constituent tables already cached for the breadth panel, then a per-symbol
lookup for whatever they miss. A symbol Yahoo has no sector for is cached as
`""` rather than `None`, because the cache reads a `None` back as a miss and
would re-fetch the dead lookup on every poll.

Rows are held to major US exchanges, to equities, and to a non-zero move —
`region: us` still returns OTC lines whose last print is days old. A gainer
screen that spills past zero once the universe runs out is cut at the sign.

### Options (Phase 11)

The largest single subsystem — ~2,000 lines across four services, ~2,500 counting the agent and its tools:

- `options_math.py` — Black-Scholes pricing, greeks, strategy arithmetic
- `options_screen.py` — liquidity, moneyness and probability filters
- `options.py` — fetch, normalise, screen, explain
- `vol_provider.py` — volatility context behind one swappable seam
  (`realized_proxy` free by default, `marketdata` paid), so the free path is
  honest about what it cannot know rather than inventing an IV rank

Screens: cash-secured puts, covered calls, LEAPS calls, put credit spreads.

---

## 5. Cross-cutting subsystems

### Tools and the registry

`tools/registry.py` — **24 tools**, declaratively registered. Adding a data
source is one file plus one registry call.

Grouped by the `category` the registry actually assigns, so these are greppable:

| Category | Tools |
|---|---|
| `market_data` | `get_company_overview`, `get_price_history`, `get_earnings_calendar` |
| `fundamental` | `get_income_statement`, `get_balance_sheet`, `get_cash_flow`, `compute_financial_ratios` |
| `sentiment` | `get_analyst_ratings`, `get_options_chain` ¹ |
| `options` | `get_options_snapshot`, `screen_option_strategies` |
| `technical` | `compute_rsi`, `compute_macd`, `compute_bollinger_bands`, `compute_moving_averages`, `compute_support_resistance` |
| `risk` | `compute_historical_volatility`, `compute_var`, `compute_beta`, `compute_sharpe_sortino`, `compute_max_drawdown` |
| `news` | `get_recent_news`, `get_sec_filings` |
| `web` | `search_web` |

¹ `get_options_chain` is registered under `sentiment`, which looks like a
slip — put/call ratio is a sentiment read, but the tool returns a chain.
Harmless today; see [roadmap.md](roadmap.md).

`registry.get_schema_for_llm()` emits OpenAI-shaped function schemas. It had no
caller from the time it was written until Phase 13 wired tool calling — worth
knowing if you find other unused-looking machinery here.

`tools/mcp_server.py` exposes the same registry over MCP.

### Model provider abstraction

`models/provider.py` + `models/factory.py`. One env var (`MODEL_PROVIDER`)
swaps between **openai, anthropic, qwen, ollama, vllm**.

Every provider exposes a **fast** and a **smart** tier, so a cheap call
(query decomposition, an explanation) and an expensive one (synthesis) are a
config choice rather than a hard-coded model id:

| Provider | Fast | Smart |
|---|---|---|
| openai | `gpt-4o-mini` | `gpt-4o` |
| anthropic | `claude-haiku-4-5` | `claude-sonnet-4-6` |
| qwen | `qwen2.5-7b-instruct` | `qwen2.5-72b-instruct` |
| ollama | `qwen2.5:7b` | `qwen2.5:14b` |
| vllm | `Qwen2.5-7B-Instruct` | `Qwen2.5-72B-Instruct` |

`complete()` takes `tools`/`tool_choice`; Anthropic's different tool format is
translated by pure functions in `provider.py` (tested in
`test_provider_tool_translation.py`). `services/llm_guard.py` answers whether
the configured provider can actually be called, so the UI can degrade rather
than throw.

### RAG

Fully built, running on **pgvector inside Postgres** — Qdrant was removed in
Phase 6 rather than run a second datastore.

```
ingestion/loader.py    SEC filings + news + uploads
        ↓
ingestion/chunker.py   512 tokens, 50 overlap
        ↓
ingestion/embedder.py  BAAI/bge-small-en-v1.5 (local) or OpenAI
        ↓
ingestion/upserter.py  → document_chunks, ticker_entities
```

Retrieval (`retrieval/pipeline.py`) is three stages:

1. **Decompose** — fast model splits the query into ~3 sub-queries
2. **Retrieve** — dense (pgvector) + sparse (BM25), merged by reciprocal rank
   fusion (`retrieval/fusion.py`)
3. **Rerank** — cross-encoder `BAAI/bge-reranker-base`, lazy-loaded on first use

`context/builder.py` assembles the final context under a token budget.

`rag_prefetch_node` runs it inline: if a ticker is not yet indexed it ingests
on the spot, then retrieves. Gated on `state["use_rag"]`, so the fast path
skips it entirely.

> The module docstring in `orchestrator/graph.py` still says
> `rag_prefetch_node (stub for Phase 1)`. That comment is stale — the node is
> real. It is the single most misleading line in the codebase.

### Persistence

Postgres via SQLAlchemy async + Alembic (`db/`). Nine tables:

| Table | Holds |
|---|---|
| `users` | Identity |
| `analysis_runs` | Every analysis, its report, metrics, cost |
| `watchlists` | Saved tickers + per-ticker notes |
| `user_preferences` | Risk tolerance, sectors, horizon |
| `feedback` | Thumbs up/down per run |
| `document_chunks` | RAG vectors (pgvector) |
| `ticker_entities` | Entity graph for retrieval |
| `daily_bars` | Settled OHLCV, raw + adjusted |
| `bar_coverage` | Which ranges are known-complete |

Migrations run through `python -m app.db.migrate` (`make migrate`), **not**
bare `alembic upgrade head` — it stamps a schema previously built by dev
`create_all` before upgrading. Bare alembic fails with
`relation "users" already exists`.

Redis (`cache/redis_client.py`, `memory/`) backs the L2 cache and chat session
history.

### Observability

- `observability/metrics.py` — 14 Prometheus metrics: RAGAS scores, latency,
  tool calls, guardrail trips, tokens, cost
- `observability/monitoring_client.py` — MLflow (default, :5050) or W&B; every
  run logged with params, metrics and prompt versions
- `observability/ragas_evaluator.py` — online faithfulness / relevancy /
  precision scoring inside `persistence_node`
- Grafana on :3000, 5 rows / 14 panels, auto-provisioned

### Frontend

React 18 + Vite + TypeScript + Tailwind. Routes:

| Route | Page | What it is |
|---|---|---|
| `/charting` | `ChartingPage` | Default. Lightweight Charts v5, technicals, options |
| `/market` | `MarketPage` | Indices, breadth, movers, heatmap, sectors, events |
| `/analyze` | `AnalyzePage` | Multi-agent run with live SSE agent tracker |
| `/watchlist` | `WatchlistPage` | Saved tickers |
| `/history` | `HistoryPage` | Past runs |
| `/settings` | `SettingsPage` | Provider and preferences |

Conventions worth keeping:

- `lib/api.ts` is the only place that knows URLs; `lib/types.ts` mirrors the
  backend payloads by hand (no codegen).
- `hooks/useSSE.ts` + `lib/sse.ts` handle streaming; `useQuotes` is the single
  polling seam — swapping to a real-time provider is one file.
- `context/ScreenContext.tsx` tells the chat agent what is on screen. This is
  how the floating agent knows which ticker you are looking at.
- Components under `components/ui/` are shadcn-style primitives; everything
  else is grouped by feature (`analysis/`, `chart/`, `chat/`, `market/`).

---

## 6. Configuration

`.env` lives at the **project root**, not in `backend/`. `config.py` walks up
the tree to find it — a `backend/.env` is silently ignored, which is the single
most common setup mistake.

**Required:** `OPENAI_API_KEY` (or the key for whichever `MODEL_PROVIDER`).

**Optional, degrade gracefully when unset:**

| Var | Without it |
|---|---|
| `NEWSAPI_KEY` | Sentiment agent loses news |
| `TAVILY_API_KEY` | No web search tool |
| `FRED_API_KEY` | Economic-releases panel shows "not configured" |
| `ALPHA_VANTAGE_KEY` | Unused fallback |
| `MARKETDATA_API_KEY` | Volatility falls back to the realized proxy |
| `WANDB_API_KEY` | MLflow only |

**Tuning knobs that change behaviour:**

```
MODEL_PROVIDER=openai            # openai|anthropic|qwen|ollama|vllm
CHAT_MODEL=fast                  # which tier the chat agent uses
CHAT_MAX_TOOL_ITERATIONS=3       # the cycle cap in the chat graph
CHAT_MAX_TOOL_CALLS=4
CHAT_HISTORY_TURNS=12
RAG_TOP_K=8
RAG_CHUNK_SIZE=512
EMBEDDING_PROVIDER=local         # local (bge-small) or openai
VOL_DATA_PROVIDER=realized_proxy # or marketdata (paid)
MONITORING_BACKEND=mlflow        # mlflow|wandb|none
```

Host ports are overridable so a local Postgres or Redis does not block the
stack: `BACKEND_PORT`, `FRONTEND_PORT`, `POSTGRES_PORT`, `REDIS_PORT`.

---

## 7. Testing

```bash
make test       # unit only — no DB, no network
make test-all   # adds integration, needs real PostgreSQL
make eval       # RAGAS against the golden dataset
```

**360 unit tests across 17 files.** CI (`.github/workflows/ci.yml`) runs
`pytest tests/unit -m "not network"` plus `tsc --noEmit` and a frontend build
on every PR.

The suite is heaviest exactly where the logic is subtle and least covered where
it is I/O:

| File | Tests what |
|---|---|
| `test_options_screen.py` | Every liquidity/moneyness filter, individually |
| `test_options_math.py` | Black-Scholes and greeks against known values |
| `test_signals.py` | Each technical condition, and the no-advice property |
| `test_market_movers.py` | Screen filters, cap-tier partition, week arithmetic |
| `test_chat_guardrails.py` | Advice detection and numeric grounding |
| `test_vol_provider.py` | Every provider returns an identical key set |
| `test_cache_tiers.py` | L1/L2, single-flight, stale-fallback |
| `test_provider_tool_translation.py` | OpenAI ↔ Anthropic tool schemas |

**The house style is worth matching:** one behaviour per test, a name that
states the claim as a sentence (`test_a_gainer_screen_never_returns_a_decliner`),
and a docstring on the non-obvious ones explaining what would break. Filters
get a test each, because "why is this row missing" is the question the code
will be asked most often.

Pure functions are the seam — `signals.py`, `options_math.py`, `bar_derive.py`
and the screen filters are all pure precisely so they can be tested without a
network.

> `tests/unit/test_tools.py` makes **live yfinance calls** despite living in the
> unit suite. It is the one exception, and it is why CI carries a `not network`
> marker.

---

## 8. Running it

The containerized stack is the intended path and the only one that gives you
pgvector without a local install:

```bash
cd infra && docker compose up -d
```

Migrations run as their own service before the API starts. If Docker Desktop
itself is not running, `docker compose` fails with a socket error — `open -a
Docker`, wait for the daemon, retry.

Native, if you have pgvector locally:

```bash
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev
```

Plain `postgresql@15` from Homebrew has no `vector` extension; the app fails at
startup with `extension "vector" is not available`. `brew install pgvector`
fixes it.

### Parallel work with git worktrees

**Branch every feature off `dev`, never off the previous feature.** Phases 10
through 13 were each cut from the tip of the one before, which made them a
stack rather than four parallel tracks — see `CLAUDE.md` for the full account
of how that goes wrong and how to unstack it.

```bash
git worktree add ../agent_invest-<name> -b <branch> dev
```

Symlink the gitignored pieces rather than copying — `.venv` is 1.3 GB and
`node_modules` 159 MB:

```bash
MAIN=/Users/yusali/dev/agent_invest
ln -sfn "$MAIN/backend/.venv"         <worktree>/backend/.venv
ln -sfn "$MAIN/frontend/node_modules" <worktree>/frontend/node_modules
ln -sf  "$MAIN/.env"                  <worktree>/.env
```

Only one worktree can drive the Docker stack on the default ports —
`infra/docker-compose.yml` bind-mounts `../backend/app` and `../frontend`
relative to itself, so **whichever branch is checked out in the main tree is
what the stack serves.**

---

## 9. Gotchas

Things that have cost time before, in rough order of how much:

1. **`.env` must be at the project root.** `backend/.env` is ignored silently.
2. **Stacked branches.** A feature cut from another feature inherits its bugs,
   cannot merge independently, and makes a bisect point at the wrong commit.
3. **`alembic upgrade head` fails on a `create_all` schema.** Use `make migrate`.
4. **The Docker stack serves the main worktree**, not the one you are editing in.
5. **A cached `None` reads back as a cache miss.** Store a sentinel (`""`) if
   you need to cache a negative result.
6. **yfinance drops symbols from large batches.** Always retry the missing set
   and gate caching on completeness, or the gap pins for the whole TTL.
7. **`pandas-ta` is not installed** — we use `ta` (Python 3.11 compatible).
8. **Makefile recipes need hard tabs.**
9. **Yahoo and GICS disagree on four sector names.** Normalise through
   `market_data.normalise_sector` before displaying them in one list.
10. **Times are anchored to America/New_York, not UTC.** After 8pm ET the UTC
    date has rolled over; a UTC anchor drops today's events off the calendar
    and, on a Sunday night, hands back the wrong week.

---

## 10. Where to start reading

If you are picking this up cold, in order:

1. `backend/app/agents/orchestrator/graph.py` — the fan-out, and the system's thesis
2. `backend/app/agents/base/schemas.py` — the contracts everything else obeys
3. `backend/app/services/signals.py` — the clearest example of the house style:
   pure, deterministic, and designed so a guardrail can be honest
4. `backend/app/services/cache.py` — every market read goes through it
5. `backend/app/tools/registry.py` — how a data source gets added
6. `frontend/src/pages/MarketPage.tsx` — the busiest UI surface
