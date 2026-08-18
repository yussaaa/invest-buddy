# Roadmap and open work

What is built, what is not, and what is documented as built but is not — each
line checked against the code on **2026-08-18**, not copied forward from the
previous plan.

Status here supersedes the phase list anywhere else. `CLAUDE.md`'s phase table
had drifted badly enough to be actively misleading (it described Phase 3 as
"currently stubbed" while 1,063 lines of working RAG sat in `app/rag/`).

---

## Shipped

| Phase | What | Verified by |
|---|---|---|
| **1** Core engine | 6 agents, LangGraph fan-out, guardrails, synthesiser, SSE, React shell | `orchestrator/graph.py` |
| **2** Persistence | Postgres, 2 Alembic revisions, Redis session memory, repositories | `db/`, `memory/session.py` |
| **3** RAG | Ingestion → chunk → embed → pgvector; decompose + dense/BM25/RRF + cross-encoder rerank | `rag/` (1,063 lines) |
| **5** Eval + observability | Online RAGAS, MLflow, 14 Prometheus metrics, Grafana | `observability/` |
| **6** Kubernetes | 14 manifests, minikube 9/9 pods, HPA 2–5 replicas | `infra/k8s/` |
| **7** Documentation | `business_logic.md`, `evaluation_framework.md`, `model_selection.md` | `docs/` |
| **8** Market dashboard | Charting + Market tabs, breadth, heatmap, watchlist rail | `pages/`, `services/market_data.py` |
| **9** Caching + bar store | Redis L2, `daily_bars` store, technicals cut over | `services/cache.py`, `bar_store.py` |
| **10** UI polish | Collapsible/resizable sidebar, persisted chart toolbar | `App.tsx` |
| **11** Options | ~2,400 lines: math, screener, vol provider, specialist agent | `services/options*.py` |
| **12** Drawdown | Distance below the 52-week high | `services/drawdown.py` |
| **13** Chat agent | Bounded ReAct cycle, deterministic signals, chat guardrails | `agents/chat/` |
| **14** Movers + week nav | Cap-tier movers with sectors, navigable events week | `services/market_data.py` |
| **15** Client cache | TTL cache above the router; instant tab switches, persisted toolbars | `frontend/src/lib/clientCache.ts` |

Phases 10–12 and 14 were never written into the phase list. They are recorded
here and summarised in `CLAUDE.md`.

---

## Not built

### Phase 4 — Memory + guardrails v2

The only wholly unstarted phase.

- [ ] Long-term user profile learning (preferred sectors, risk drift)
- [ ] Conversation memory across sessions — `memory/session.py` is per-session
      today; nothing accumulates
- [ ] Citation-completeness enforcement (currently checked, not enforced)
- [ ] Confidence recalibration
- [ ] **Investment-advice rewriting rather than detection.** The chat agent
      already rebuilds a failed reply from its signal set
      (`chat/graph.py:_hedged_reply`); the analysis path only flags. Lifting
      that mechanism across is the tractable piece.

### Phase 7 leftovers

The three written documents exist. Not done:

- [ ] Architecture diagrams as images (there are ASCII ones in
      [architecture.md](architecture.md))
- [ ] Demo video of a streaming multi-agent run
- [ ] README screenshots

---

## Open items in shipped work

### Correctness / hygiene

- [ ] **`get_options_chain` is registered under category `sentiment`** in
      `tools/registry.py`, not `options`. Harmless — nothing dispatches on
      category — but it misleads anyone reading the registry.
- [ ] **Frontend test coverage is one file.** `clientCache.test.ts` (18 tests,
      vitest) covers the cache; the hook and the pages are unverified except by
      `tsc` and the manual checklist. Testing them needs jsdom +
      testing-library + msw, which is a real dependency jump — deliberate for
      now, revisit if the pages start regressing.
- [ ] **`tests/unit/test_tools.py` makes live network calls.** It is in the
      unit suite and is why CI needs `-m "not network"`. Should be mocked or
      moved to integration.
- [x] ~~Stale docstring in `orchestrator/graph.py` calling `rag_prefetch_node`
      a "stub for Phase 1"~~ — fixed 2026-08-18. It was what misled the phase
      list; the graph diagram there now matches the code.
- [x] ~~`AGENTS.md` had drifted ~214 lines from `CLAUDE.md`~~ — replaced
      2026-08-18 with a pointer, so it cannot drift again. Still untracked;
      commit it if Codex is part of the workflow.

### Performance

- [ ] `get_history` / `get_overview` / `get_breadth` still fetch live rather
      than reading `daily_bars`. The store is only wired into technicals.
- [ ] **No backfill job** — the bar store warms lazily, so the first request
      for a cold ticker pays full provider latency.
- [ ] **The movers earnings calendar costs ~36s cold**, once per 6h globally
      (Redis L2 + single-flight). Week navigation is free after that, but the
      first visitor after expiry waits. A warm-up job would remove it; there is
      no scheduler in the stack yet.
- [x] ~~`/market/breadth` never served warm~~ — fixed 2026-08-18. `cached`
      stamps a value with the time its fetch *started*, so `BREADTH_TTL = 60`
      against a 120 s+ scan produced values that were **born expired** and could
      never satisfy a lookup. Every request paid for a full cold scan, and a
      60 s client poll left two or three permanently in flight, eating the
      browser's per-origin connection budget. TTL raised to 900 s, client poll
      to 600 s. `cache.py` now logs `cache_ttl_shorter_than_fetch` so the next
      instance of this is loud rather than invisible.
- [ ] **`overview` hits the same trap under load** — that new warning has
      already caught it once at `seconds=46.7 ttl=30.0`, when yfinance was rate
      limiting. Unlike breadth it is normally far inside its TTL, so this is
      load-dependent rather than permanent. Either raise the TTL or stamp slow
      producers at completion.
- [ ] **The TradingView heatmap rebuilds on every mount** — a re-injected
      `<script>`, ~0.5–2 s of blank rectangle. It cannot be mutated in place, so
      only keep-alive fixes it. Now the *only* remaining loading artefact on
      `/market`, so it reads more prominently than it used to.
- [ ] **Chart zoom and pan are lost on navigation** even though the data is
      instant — a new canvas per mount. Cheap fix: stash
      `timeScale().getVisibleLogicalRange()` in the client cache on unmount.
- [ ] **Quote polling is not shared between overlapping symbol sets.**
      `useQuotes(['AAPL'])` and the watchlist rail are different cache keys, so
      they poll separately for the same symbol. Mirroring the backend's
      `get_fresh_many`/`put_many` per-symbol keying would unify them.
- [ ] A chat turn is ~8–9s cold, most of it the two model calls.

### Cosmetic

- [ ] Chat model sometimes writes signal ids inline as well as in the trailing
      citation line.
- [ ] The `net_bias` chip can read "Leaning bullish" while the prose says
      "mixed" — both correct when a weak short-term signal opposes a strong
      long-term one, but the wording should agree.

---

## Accepted trade-offs

Not bugs, and not worth relitigating without new information:

| Decision | Why | What would change it |
|---|---|---|
| **No WebSocket price feed** | yfinance has no streaming API and is delayed anyway; the UI polls | A paid provider (Polygon/Alpaca/Finnhub). `useQuotes` is the single seam |
| **Heatmap is TradingView's embed** | Theirs is genuinely real-time; ours was delayed | Accepting delayed tiles that link internally |
| **No bid/ask** | Not available from the data source | A paid feed |
| **No verdict badge in chat** | "Should I buy?" is answered as a conditional setup — more defensible, and it makes the guardrail coherent | Nothing; this is the design |
| **No LangGraph checkpointer** | Only `InMemorySaver` ships with the installed version; survives neither restart nor a second worker | A durable checkpointer landing upstream |
| **Chat gather is a separate model call** | Buys a streaming path where no tool request arrives mid-sentence | Nothing; the cheap call is worth it |
| **pgvector over Qdrant** | One datastore instead of two | Scale that Postgres cannot serve |

---

## If you are picking this up next

Ranked by value against effort:

1. **Phase 4 advice rewriting** — the mechanism already exists in the chat
   path; lifting it to the analysis path is contained work with a visible
   result.
2. **Demo video + README screenshots** — cheapest possible improvement to how
   this reads as a portfolio project, and the only outstanding Phase 7 item.
3. **Fix `test_tools.py`** — small, removes the CI network dependency, and
   makes `make test` honest about being offline.
4. **Serve `get_history`/`get_overview` from the bar store** — the store and
   the fallback already exist; this is wiring plus tests.
5. **Long-term memory** — the largest remaining piece, and the one that most
   changes what the product can claim.
