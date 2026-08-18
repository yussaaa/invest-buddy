# AGENTS.md — Project Context

Agent Invest is a multi-agent stock investment analysis platform: LangGraph
orchestrates 6 specialist agents (Fundamental, Sentiment, Technical, Risk,
Market Research, Options) that fan out in parallel, each calling
domain-specific tools, producing structured Pydantic outputs that are checked
by guardrails and synthesised into a final report.

**Backend** Python 3.11, FastAPI, LangGraph, Pydantic ·
**Frontend** React 18, Vite, TypeScript, Tailwind ·
**Data** Postgres (+pgvector), Redis, yfinance ·
**Models** OpenAI (default), Anthropic, Qwen, Ollama, vLLM

## Read these — do not duplicate them here

This file used to be a full copy of `CLAUDE.md` and drifted ~214 lines out of
date, which is worse than having no file at all. It is now a pointer, on
purpose. Put project context in `CLAUDE.md`; everything reads from there.

| File | For |
|---|---|
| **[CLAUDE.md](CLAUDE.md)** | Orientation: structure, how to run, conventions, gotchas. **Read this first.** |
| **[docs/architecture.md](docs/architecture.md)** | The detailed reference — every subsystem, the three request flows, and why the awkward bits are that way |
| **[docs/roadmap.md](docs/roadmap.md)** | What is built and what is not, verified against the code |
| [docs/business_logic.md](docs/business_logic.md) | Data sources and domain rules |
| [docs/model_selection.md](docs/model_selection.md) | Model tradeoff write-up |
| [docs/evaluation_framework.md](docs/evaluation_framework.md) | Evaluation methodology |

## The short version

```bash
cd infra && docker compose up -d   # the intended path; gives you pgvector
make test                          # 360 unit tests, no DB or network
make migrate                       # not bare `alembic upgrade head`
```

- `.env` goes at the **project root**, not in `backend/`.
- Branch every feature off `dev`, never off another feature.
- The Docker stack serves whichever branch is checked out in the **main**
  worktree.
