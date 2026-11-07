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
| **16** Trend + valuation | SMA slope, z-score and σ band; reverse-DCF valuation panel; collapsible charting sections | `services/trend.py`, `services/valuation*.py` |
| **17** Signals | Reddit mention board (ApeWisdom) + StockTwits self-tags, deterministic crowd rules, Yahoo preset screens, hourly mention history, `/signals` page, cited research agent | `services/social/`, `services/social_signals.py`, `agents/research/` |

Phases 10–12 and 14 were never written into the phase list. They are recorded
here and summarised in `CLAUDE.md`.

---

## Not built

### Phase 4 — Memory + guardrails v2

The only wholly unstarted phase.

- [ ] Long-term user profile learning (preferred sectors, risk drift)
- [ ] Conversation memory across sessions — `memory/session.py` is per-session
      today; nothing accumulates
- [ ] Citation-completeness enforcement (currently checked, not enforced) —
      **partly done in Phase 17**: the research agent strips citations that
      resolve to no source and drops uncited drivers
      (`agents/research/graph.py:verify`). The analysis path still only checks.
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

- [x] ~~`financial_ratios.fcf_yield` read `info["freeCashflow"]`~~ — fixed
      2026-08-18. That field reports MSFT at $16.5bn against $67bn on its own
      cash flow statement, turning a ~1.9% yield into 0.46%. It was the single
      custom calculation in the fundamentals stack, and it had been wrong since
      it was written. Now reads the statement, with `info` as fallback.
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
- [x] ~~**The week calendar showed two or three reports a day**~~ — fixed
      2026-11-07. It scanned a fixed list of 40 mega-caps, so once those had
      reported a peak-season day of ~80 large-cap reports showed a couple. Now
      one Yahoo calendar query per week (`yf.Calendars`) covers every NYSE /
      Nasdaq listing at $10B+, in under a second, which also retires the 36s
      cold fetch above. Columns show the eight largest, grouped before-open /
      after-close / time TBA, and fold the rest.
      The header shows season progress ("Q3 2026 season · 703 / 863 large
      caps reported"): the same filter over the quarter the week falls in.
      A cap-tier toggle (All / Large / Mid / Small, the movers' `CAP_TIERS`)
      filters both; large by default, remembered per browser.
- [x] ~~**Economic releases matched FRED names by substring**~~ — fixed
      2026-11-07. "Consumer price index" also matched "Research CPI" and "CPI
      excluding energy commodities", "gross domestic product" matched "GDP by
      County", and the UMich entry named a release FRED does not have. Now an
      exact-name table with trader labels, plus jobless claims, ADP and ECI. A
      keyless or failed FRED answer is no longer cached, so adding the key
      shows at once instead of after 30 minutes.
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
- [x] ~~Chart MA200 started 200 bars into the range~~ — fixed 2026-11-04. The
      average was computed from the visible candles alone, so on 1Y it drew only
      the last ~50 sessions. Store-backed ranges (3M–5Y) now return `ma_lead`,
      up to 199 closes before the first candle, and `PriceChart`'s `sma` seeds
      its window with them.
- [ ] **Intraday ranges (1D / 5D / 1M) and the live fallback still have no
      lead**, so a long average on 5-minute bars starts late or not at all. A
      longer yfinance period trimmed back to the visible sessions would fix it.

### Guardrails

- [x] ~~**Numeric grounding accepted ~31% of random prices**~~ — fixed 2026-11-03.
      The ±2% tolerance plus a round-to-integer clause let "$199" ground 200.2.
      A figure now grounds only if a reading rounds or truncates to it at the
      precision it was written with; `tests/unit/test_grounding_rate.py`
      measures 0.09% at two decimals, 0.9% at one, 8% for whole dollars.
      Magnitude suffixes ("63.8M") are scaled first — a live reply was being
      blocked for one — and window labels ("the 200-day") are not claims.

---

## Opened by Phase 17

Not built yet, in rough order of value:

- [ ] **Wikipedia pageviews as a second attention source.** Free, keyless
      (needs a User-Agent), and 60+ days of daily history on day one — a real
      z-score while our own ApeWisdom history is still filling. Needs a
      ticker → article mapping, cached for a week.
- [ ] **Put/call volume signal** from the option chain `services/options.py`
      already fetches — positioning with no new source.
- [ ] **Insider purchases** via yfinance `insider_purchases`; easier than
      parsing Form 4, which `get_sec_filings` does not fetch.
- [ ] **A custom "unusual volume" screen** as an `EquityQuery`, once the
      accepted fields are confirmed.
- [ ] **Lexicon scoring (VADER) for untagged StockTwits messages** — more than
      half are untagged. A new dependency, so it changes the shared `.venv`.
- [ ] **Real Reddit API and X providers.** Both are placeholders on the same
      interface today (`services/social/placeholders.py`); they report what is
      missing and return a caveat.
- [ ] **The trending overlay on screens uses L1 only** (`screens.reddit_overlay`
      peeks the board cache), so a fresh replica shows no Reddit ranks until
      its trending table has loaded once.

Rejected: **pytrends (Google Trends).** Unofficial, unmaintained, and
rate-limited routinely; "Google search" is covered by Tavily (`search_web`),
which the research agent uses.

Fixed on the way in (2026-11-03):

- [x] ~~Chat grounding never saw tool results~~ — `_grounding_evidence` passed
      `tool_records` (name, latency, success), so any figure quoted from a tool
      read as invented. Payloads are now kept (`agents/base/tool_loop.py`).
- [x] ~~`prepare_context_node` passed the ticker as a string~~ to
      `get_events(extra_symbols=...)`, which iterated it into letters.
- [x] ~~httpx 4xx responses were retried~~ three times with backoff; only 429
      and 5xx are now (`tools/retry_decorator.py`).

---

## Market data beyond Yahoo (opened 2026-11-04)

Almost every number in the app comes from yfinance: `.info` (78 call sites —
profile, valuation inputs, ratios), `.history` and `yf.download` (bars),
`.options` / `.option_chain` (the whole options phase), `.calendar` /
`get_earnings_dates`, the statements, and `yf.screen` (Signals screens). It is
free and unofficial, so there is no SLA and the throttle is per IP.

**How it fails is the real problem.** yfinance's `.info` is two requests —
quoteSummary (beta, sector, industry, short interest, `dayLow`,
`averageVolume`) and the v7 quote (price, market cap, P/E, 52w range). When
quoteSummary is throttled it returns the v7 half and raises nothing. Two
consequences found so far:

- [x] ~~Profile pinned blank beta / avg volume / day range for every symbol~~ —
      fixed 2026-11-04 on `feat/ux-improvement`. A partial profile is served
      but never cached (`should_cache`), and day range and average volume fall
      back to the v7 quote's own fields. Beta and sector have no fallback.
- [ ] **Valuation silently prices a throttled symbol with beta = 1.0.**
      `cost_of_equity(None)` defaults rather than failing, and the result is
      cached for 15 minutes. AAPL's 1.09 → 1.0 moves the discount rate, so the
      reverse-DCF headline moves with it. Same `partial` guard, or the derived
      beta below.

### Tier 0 — free, no new vendor

Most of what quoteSummary supplies can be computed from data already held,
which is both more robust and more accurate (a stated method rather than
Yahoo's undocumented one):

- [ ] **Beta from `daily_bars`** against SPY — state the convention (5y monthly
      matches Yahoo; 2y weekly is the common alternative). Removes the valuation
      dependency above and makes beta reproducible.
- [ ] **Average volume from `daily_bars`** (3-month mean) and today's range from
      the intraday quote already fetched for the header.
- [ ] **Sector from the GICS constituent tables** `market_data` already loads,
      through `normalise_sector`; Yahoo's names only for symbols outside them.
- [ ] **Fundamentals from SEC EDGAR `companyfacts`** — free, no key, 10 req/s
      with a descriptive User-Agent. Filings-grade figures for the DCF, where
      `.cashflow` is Yahoo's restatement of them.
- [ ] **A Yahoo request governor** — one process-wide limiter (concurrency cap
      plus token bucket) for every yfinance call, and a breaker on 429 like the
      StockTwits one. The Signals page's profile fan-out is the likeliest
      trigger today; nothing coordinates it with the chart page's calls.

### Tier 1 — a `MarketDataProvider` seam

The same move as `ModelProvider`: one interface (bars, quote, profile,
fundamentals, option chain, calendar), yfinance as the first implementation,
and an env var to choose. It turns every row in the table below into a config
change, and fallbacks into a list rather than code.

### Tier 2 — paid or keyed sources

Prices checked 2026-11-04. *Official* = read on the vendor's own page;
*third-party* = aggregator sites, which disagreed with each other, so confirm
before buying.

| Provider | Free | Paid entry | Good for | Gaps / caveats | Source |
|---|---|---|---|---|---|
| **Alpaca Market Data** | Basic: IEX-only real-time, 200 calls/min, history since 2016, 30 WebSocket symbols, indicative options | Algo Trader Plus **$99/mo**: all US exchanges (SIP), 10k calls/min, OPRA options | Bars, quotes, streaming — the `useQuotes` WebSocket seam; options on the paid tier | No fundamentals, no sector/beta. Needs an Alpaca (paper) account | Official |
| **Tiingo** | 50 req/hr, 1,000/day, 500 symbols/mo | Power **$30/mo** ($300/yr): 10k/hr, 100k/day, 30+ yrs prices, 15+ yrs fundamentals; Business $50/mo | Validated EOD bars; cheapest reliable history | **Internal use only — may not display or share**; fundamentals add-on priced by sales | Official |
| **Financial Modeling Prep** | 250 calls/day, EOD | Starter **$19/mo** annual ($29 monthly), 300/min; Premium $49 ($69); Ultimate $99 ($139) | Profile (beta, sector, avg volume), statements, earnings calendar, screener — the closest single replacement for `.info` | Redistribution needs a separate licence | Third-party transcription of the pricing page (2026-10-28); our fetch was refused |
| **Massive** (formerly Polygon) | — | Stocks Advanced **$199/mo** (real-time, 20+ yrs, WebSockets, financials); Financials & Ratios $29/mo | Real-time and tick history | Lower stock tiers ($0/$29/$79 per aggregators) not on the official page as fetched; options a separate ladder (~$398/mo with real-time stocks, third-party) | Official (partial) |
| **Finnhub** | 60 calls/min; US quotes, basic fundamentals, earnings calendar, company news | ~$50/mo per market (unverified; sources range to $3,500 for all-in-one) | Free earnings calendar and news — the dead NewsAPI key's replacement | Free tier non-commercial; paid pricing opaque | Third-party |
| **EODHD** | Limited | $19.99 EOD → $29.99 +intraday → $59.99 fundamentals → $99.99 all-in-one; 100k weighted calls/day | Global coverage, one bill | Personal-use tier; commercial from $399 | Third-party (consistent) |
| **Twelve Data** | Small | Grow $49 / $79, Pro $229 | — | A business plan is required for *any* company use | Official |
| **Alpha Vantage** | 25 req/day | $49.99 (75/min) → $249.99 (1,200/min) | — | Free tier too small for a watchlist | Third-party |
| **ThetaData** (options) | EOD tier | ~$40 / $80 / $160 per mo | Per-contract IV, greeks, history | Index underlyings extra; no vol surface | Third-party (rechecked 2026-09) |
| **Tradier** (options) | Free with a brokerage account (unverified) | — | Chains with ORATS-computed greeks/IV | Brokerage account | Third-party |
| **Interactive Brokers** | Cboe One + IEX real-time US stocks for account holders | ~$10/mo non-pro bundle, waived at $30/mo commissions | Real-time quotes and chains on an existing account | Needs TWS / IB Gateway running; personal use | Third-party + IBKR KB |
| **Databento** | $125 credits | Standard $199/mo live; usage-based history | Tick / L2 | Overkill for EOD analytics | Official blog |

**Recommendation, in order:**

1. **Tier 0 first ($0).** Derived beta, average volume and sector, the governor,
   and the `partial` guard on valuation remove the failures seen so far without
   a vendor.
2. **Then the seam, then FMP Starter (~$19–29/mo) + Alpaca Basic (free).** FMP
   replaces `.info`, statements, the earnings calendar and the screener; Alpaca
   takes bars, quotes and a WebSocket feed. yfinance stays as the fallback and
   for option chains.
3. **Alpaca Algo Trader Plus ($99/mo) only if real-time matters** — SIP quotes
   plus OPRA options in one subscription, retiring Yahoo for the options phase
   too. Confirm its option snapshots carry IV and greeks before relying on it.
4. **Not Massive, Databento, or Twelve Data** for this app — real-time tick data
   or business-only licensing at several times the price, for analytics built
   on daily bars.

**Licensing is the constraint most likely to bite.** Nearly every individual
plan is personal or internal use, and Tiingo's says outright that data may not
be displayed to anyone else. A publicly deployed demo serving live data to
visitors would need a display licence; the portfolio demo should run on the
author's key in a recording, or on a cached snapshot.

---

## Accepted trade-offs

Not bugs, and not worth relitigating without new information:

| Decision | Why | What would change it |
|---|---|---|
| **No WebSocket price feed** | yfinance has no streaming API and is delayed anyway; the UI polls | Alpaca Basic streams IEX quotes free for 30 symbols; SIP is $99/mo. `useQuotes` is the single seam — see *Market data beyond Yahoo* |
| **Heatmap is TradingView's embed** | Theirs is genuinely real-time; ours was delayed | Accepting delayed tiles that link internally |
| **No bid/ask** | Not available from the data source | A paid feed |
| **No verdict badge in chat** | "Should I buy?" is answered as a conditional setup — more defensible, and it makes the guardrail coherent | Nothing; this is the design |
| **No LangGraph checkpointer** | Only `InMemorySaver` ships with the installed version; survives neither restart nor a second worker | A durable checkpointer landing upstream |
| **Chat gather is a separate model call** | Buys a streaming path where no tool request arrives mid-sentence. Its latency is now hidden on no-tool turns by speculating the answer alongside it | Nothing; the cheap call is worth it |
| **pgvector over Qdrant** | One datastore instead of two | Scale that Postgres cannot serve |
| **StockTwits is best-effort** | Free and the only self-stated crowd direction, but it sits behind Cloudflare and refuses some server clients. A 15-minute breaker turns that into a caveat | A licensed social-data feed |
| **Mention attention never has a direction** | A spike says people are talking, not which way; extremes are as often late as early | Evidence that some attention pattern leads price, measured, not assumed |

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

---

## Opened by Phase 16

- [x] ~~Two slope numbers describe the same average~~ — fixed 2026-11-03.
      `compute_ma_ladder` now computes its slope with `trend.slope_per_day` /
      `annualise` / `classify_slope` (`slope_annualised_percent`,
      `slope_label`), so `MaRow`'s tooltip and the trend panel show the same
      number; `test_trend.py` pins that they match. `slope_percent_5d` is gone.
- [x] ~~No `explain_valuation` endpoint~~ — built 2026-11-03, guard first. The
      strict pattern now catches hedged forms (*"appears undervalued"*,
      *"looks significantly overvalued"*), and a `mode="valuation"` bans the
      verdict vocabulary in any wording. `GET /market/valuation/explain` is the
      first explainer whose output is checked before it is shown — advice in
      valuation mode plus numeric grounding against the brief the model was
      given — and on failure it returns a templated explanation flagged
      `guarded: true`, the `_hedged_reply` mechanism lifted from chat.
- [x] ~~`signals.py` still reads neither slope nor z-score~~ — fixed
      2026-11-03. `ma200_slope_rising` / `ma200_slope_falling` (trend, long)
      and `stretched_from_ma200` (volatility, low reliability) read
      `technicals["trend"]`; `"volatility"` now has a rule and a `coverage` key.
      Both run through the advice and action-verb invariant tests.
- [x] ~~**The trend summary widened the grounding evidence set**~~ from 54
      numbers to 67, and the coincidental-grounding rate on $100–400 prices from
      26.6% to 32.3%. The series stays out (it would have made it 1,258 and
      47.3%); the tolerance itself was fixed on `fix/open-items` (#10) — see
      *Guardrails* above: 0.09% at two decimals, so the extra 13 numbers no
      longer move it materially.
- [x] ~~The scenario band is asymmetric in output~~ — explained 2026-11-03.
      Still real DCF behaviour (JNJ: $126 / $246 / $799), but the scenario card
      now says so in one line when the bull case's distance above base exceeds
      1.5× the bear case's distance below, and points at the grid.
