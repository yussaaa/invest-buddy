# Roadmap and open work

What is built, what is not, and what is documented as built but is not — each
line checked against the code on **2026-08-18**, not copied forward from the
previous plan. The open items in shipped work were revisited on **2026-11-03**.

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

- [x] ~~**`get_options_chain` is registered under category `sentiment`**~~ —
      fixed 2026-11-03. Filed under `options`; its data source deliberately unchanged.
- [ ] **Frontend tests cover pure modules only.** Now three files —
      `clientCache`, `chartView` (zoom memory) and `quoteStore` (per-symbol
      quotes), 32 tests — because the logic behind the 2026-11-03 UI fixes was pulled
      into DOM-free modules on purpose. Hooks and pages are still unverified
      except by `tsc` and the manual checklist; testing them needs jsdom +
      testing-library + msw, a real dependency jump that the shared
      `node_modules` symlink would push onto every worktree. Deliberate for now.
- [x] ~~**`tests/unit/test_tools.py` makes live network calls.**~~ — fixed 2026-11-03.
      The live checks moved to `tests/integration/test_tools_live.py` (still
      `network`); `tests/unit/test_tool_registry.py` pins the same contracts
      against a fake `yfinance.Ticker`. `make test` now selects
      `-m "not network"` like CI, and the unit suite passes with the network
      blocked.
- [x] ~~**The bar store could go stale as a whole and never notice**~~ — found
      and fixed 2026-11-03, while wiring history to it. Staleness was judged only
      against SPY's *stored* bar; once SPY stopped refreshing, every symbol
      level with it looked current. The store had been frozen at 2026-08-05,
      so technicals ran on three-month-old bars with today's price appended.
      Now also stale when behind the previous weekday, at most one refetch per
      symbol per day (`last_refresh_at`), and a refresh never narrows the span
      a symbol was backfilled to.
- [x] ~~**NewsAPI errors were retried even when permanent**~~ — found and fixed
      2026-11-03. `apiKeyInvalid` carries no "401" in its text, so a dead key cost
      4.4 s of retries on every news tool call; now 0.78 s. **The configured
      `NEWSAPI_KEY` is itself invalid** — news is unavailable until it is
      replaced.
- [x] ~~Stale docstring in `orchestrator/graph.py` calling `rag_prefetch_node`
      a "stub for Phase 1"~~ — fixed 2026-08-18. It was what misled the phase
      list; the graph diagram there now matches the code.
- [x] ~~`AGENTS.md` had drifted ~214 lines from `CLAUDE.md`~~ — replaced
      2026-08-18 with a pointer, so it cannot drift again. Still untracked;
      commit it if Codex is part of the workflow.

### Performance

- [x] ~~`get_history` / `get_overview` / `get_breadth` still fetch live~~ —
      fixed 2026-11-03. Daily-and-coarser history is stored bars plus a 5-session
      download for today (identical to Yahoo for AAPL 1Y/5Y/3M; MAX now goes
      back to 1980 where Yahoo's monthly series starts in 1985). Overview prices
      warm symbols over 5 sessions instead of a month. Breadth must price today
      live, so its download stays; the stored previous close rescues symbols
      that come back with only today's row.
- [x] ~~**No backfill job**~~ — fixed 2026-11-03. `services/warmup.py`, started from
      the lifespan: at startup and after 16:30 ET daily, SPY, indices, sectors,
      macro, the earnings universe and watchlists at 5y, breadth constituents at
      1y. 63 core symbols in 8.3 s cold, 0.5 s warm. One replica does it (Redis
      lock); `WARMUP_ENABLED=false` turns it off.
- [x] ~~**The movers earnings calendar costs ~36s cold**~~ — fixed 2026-11-03. The
      warm-up loop re-fetches it at 85% of its TTL and primes the endpoint's
      own key, so no visitor waits.
- [x] ~~`/market/breadth` never served warm~~ — fixed 2026-08-18. `cached`
      stamps a value with the time its fetch *started*, so `BREADTH_TTL = 60`
      against a 120 s+ scan produced values that were **born expired** and could
      never satisfy a lookup. Every request paid for a full cold scan, and a
      60 s client poll left two or three permanently in flight, eating the
      browser's per-origin connection budget. TTL raised to 900 s, client poll
      to 600 s. `cache.py` now logs `cache_ttl_shorter_than_fetch` so the next
      instance of this is loud rather than invisible.
- [x] ~~**`overview` hits the same trap under load**~~ — fixed 2026-11-03 for every
      key: `cache.py` stamps values when the fetch *finishes*, so none can be
      born expired, and the cross-replica lock no longer expires mid-fetch.
- [x] ~~**The TradingView heatmap rebuilds on every mount**~~ — fixed 2026-11-03. The
      Market page stays mounted once visited, hidden elsewhere;
      `PageActiveContext` pauses its polls while it is off screen.
- [x] ~~**Chart zoom and pan are lost on navigation**~~ — fixed 2026-11-03, and on
      every 30 s history refresh too, which also called `fitContent`. Kept per
      symbol:range, anchored to the latest bar so a new session slides the
      window rather than clipping it.
- [x] ~~**Quote polling is not shared between overlapping symbol sets.**~~ —
      fixed 2026-11-03. `lib/quoteStore.ts` caches per symbol and batches only the
      stale ones, as the backend does.
- [ ] **A chat turn takes ~4–5 s, most of it the answer streaming.** The
      8–9 s figure was OpenAI with a cold context. 2026-11-03: the answer now starts
      alongside gather and is kept when no tool is needed, which on the
      Anthropic fast tier took time-to-first-token from 0.7–1.0 s to 0.4–0.7 s
      and totals from 4.5–5.0 s to 3.8–4.6 s. What remains is generation
      length (~250 words). The OpenAI key in `.env` currently has no credits,
      so OpenAI was not re-measured.

### Cosmetic

- [x] ~~Chat model sometimes writes signal ids inline~~ — fixed 2026-11-03. The
      prompt says not to, and `verify_node` rewrites any id from the signal set
      to its label.
- [x] ~~The `net_bias` chip can read "Leaning bullish" while the prose says
      "mixed"~~ — fixed 2026-11-03. `SignalSet.bias_phrase` ("Leaning bullish, with the
      horizons disagreeing") is the one phrase the chip, the prompt and the
      blocked-reply summary all use.

### Guardrails

- [x] ~~**Numeric grounding accepted ~31% of random prices**~~ — fixed 2026-11-03.
      The ±2% tolerance plus a round-to-integer clause let "$199" ground 200.2.
      A figure now grounds only if a reading rounds or truncates to it at the
      precision it was written with; `tests/unit/test_grounding_rate.py`
      measures 0.09% at two decimals, 0.9% at one, 8% for whole dollars.
      Magnitude suffixes ("63.8M") are scaled first — a live reply was being
      blocked for one — and window labels ("the 200-day") are not claims.

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
| **Chat gather is a separate model call** | Buys a streaming path where no tool request arrives mid-sentence. Its latency is now hidden on no-tool turns by speculating the answer alongside it | Nothing; the cheap call is worth it |
| **pgvector over Qdrant** | One datastore instead of two | Scale that Postgres cannot serve |

---

## If you are picking this up next

Ranked by value against effort:

1. **Phase 4 advice rewriting** — the mechanism already exists in the chat
   path; lifting it to the analysis path is contained work with a visible
   result.
2. **Demo video + README screenshots** — cheapest possible improvement to how
   this reads as a portfolio project, and the only outstanding Phase 7 item.
3. **Long-term memory** — the largest remaining piece, and the one that most
   changes what the product can claim.
4. **Replace the NewsAPI key and top up OpenAI** — both configured credentials
   are dead, so news is unavailable and the default provider fails every call.
