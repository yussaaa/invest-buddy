# Model Selection Tradeoffs

This document explains the reasoning behind model choices in Agent Invest. The system uses a **cost-tier split** — cheap/fast models for simple tasks, expensive/smart models only where reasoning quality matters — and abstracts all providers behind a `ModelProvider` interface so swapping is one env var change.

## The Decision Framework

Every LLM call in the system is classified into one of two tiers:

| Tier | Use Case | Requires | Example Models |
|------|----------|----------|----------------|
| **Fast** | Classification, extraction, pattern matching | Speed, low cost, structured output | gpt-4o-mini, Claude Haiku, Qwen2.5-7B |
| **Smart** | Multi-step reasoning, qualitative judgment, synthesis | Reasoning depth, nuance | gpt-4o, Claude Sonnet, Qwen2.5-72B |

The key insight: **not every LLM call needs GPT-4o**. The query classifier, sentiment classifier, and technical indicator interpreter are all pattern-matching tasks — a 7B model handles them as well as a frontier model, at 10x lower cost and 3x lower latency.

## Current Model Assignments

| Task | Tier | Default Model | Why This Tier |
|------|------|--------------|---------------|
| Query classification | Fast | gpt-4o-mini | Intent detection + JSON output. No reasoning needed. |
| Market Research agent | Fast | gpt-4o-mini | Synthesizes retrieved facts. Extraction, not reasoning. |
| Sentiment agent | Fast | gpt-4o-mini | Classifies news tone, aggregates analyst ratings. |
| Technical agent | Fast | gpt-4o-mini | Interprets computed numbers (RSI=72 → overbought). |
| Fundamental agent | **Smart** | gpt-4o | Connects financial ratios to industry context, DCF assumptions. |
| Risk agent | **Smart** | gpt-4o | Multi-factor correlation reasoning, tail risk narratives. |
| Synthesizer | **Smart** | gpt-4o | Must coherently integrate 5 diverse agent outputs, weigh contradictions. |
| Hallucination judge | Fast | gpt-4o-mini | Binary grounded/ungrounded classification against evidence. |
| RAG query decomposition | Fast | gpt-4o-mini | Breaks query into sub-queries. Simple reformulation task. |

## Cost Analysis (Measured)

From production runs tracked in MLflow and Prometheus:

| Model | Tokens/Run | Cost/Run | % of Total |
|-------|-----------|----------|------------|
| gpt-4o-mini (fast tier) | ~350 | $0.0005 | 4% |
| gpt-4o (smart tier) | ~8,000 | $0.0109 | 96% |
| **Total** | **~8,350** | **$0.0114** | 100% |

Running gpt-4o for ALL tasks would cost ~$0.05/run (4.4x more) with marginal quality improvement on extraction tasks. The tier split saves ~$0.04/run — meaningful at scale.

## Provider Comparison

### Frontier APIs

| Provider | Fast Model | Smart Model | Structured Output | Tradeoff |
|----------|-----------|-------------|-------------------|----------|
| **OpenAI** (default) | gpt-4o-mini | gpt-4o | Excellent (`response_format=json_schema`) | Best structured output reliability, widest adoption |
| **Anthropic** | Claude Haiku 4.5 | Claude Sonnet 4.6 | Good (XML/JSON) | Better reasoning, slightly worse structured output |

### Open-Weight / Local

| Provider | Fast Model | Smart Model | Setup | Tradeoff |
|----------|-----------|-------------|-------|----------|
| **Ollama** | qwen2.5:7b | qwen2.5:14b | `brew install ollama` | Zero cost, runs on laptop, ~3x slower |
| **vLLM** | Qwen2.5-7B | Qwen2.5-72B | Requires GPU | Zero API cost, production-grade throughput with continuous batching |

### When to Use Each

| Scenario | Recommended Provider | Reasoning |
|----------|---------------------|-----------|
| Daily development | Ollama (qwen2.5:7b) | Zero cost, fast iteration, no API key needed |
| Demo / portfolio review | OpenAI (gpt-4o) | Best quality for showcasing the system |
| CI / automated tests | Ollama (qwen2.5:7b) | Deterministic, free, no rate limits |
| Production (high volume) | OpenAI or vLLM | Cost-optimized or self-hosted depending on volume |
| Air-gapped / compliance | vLLM (Qwen2.5-72B) | Data never leaves your infrastructure |
| Cost-sensitive startup | Mix: Ollama for fast tier, OpenAI for smart tier only | Cuts cost by ~96% on fast-tier calls |

## The ModelProvider Abstraction

All providers implement the same interface:

```python
class ModelProvider(ABC):
    async def complete(self, messages, model, response_format, temperature, max_tokens) -> LLMResponse
    async def stream(self, messages, model, temperature) -> AsyncIterator[str]
```

Swapping providers requires one env var change:

```bash
MODEL_PROVIDER=ollama  # switches entire system to local Qwen models
```

The factory pattern (`models/factory.py`) maps the env var to the correct provider class. Both Ollama and vLLM reuse the OpenAI-compatible client with a custom `base_url` — minimal code duplication.

## Why Not Fine-Tuned Models?

For this use case, fine-tuning is not worth the investment because:

1. **Prompt engineering with structured outputs** achieves 85-90% of fine-tuning quality for extraction/classification tasks
2. **Financial data changes constantly** — a fine-tuned model trained on 2024 data misses 2025 market dynamics
3. **The tools provide the data** — the LLM's job is synthesis, not memorization
4. **Cost of fine-tuning > cost of using a larger model** for our volume (~100 runs/day)

Fine-tuning would make sense at >10,000 runs/day where the per-call savings justify the training investment, or for a specialized task like financial sentiment classification where domain-specific training data is available.

## Monitoring Model Quality

Model quality is continuously monitored via:

- **RAGAS scores** per run (faithfulness, relevancy, precision) — logged to MLflow and Prometheus
- **Hallucination score** from the guardrails node — flags drift in model accuracy
- **Prompt versioning** — every prompt has a `PROMPT_VERSION` string tracked in MLflow, enabling side-by-side comparison when prompts change
- **Golden dataset evaluation** — 10-query offline eval suite (expandable to 50) for regression testing

If a model update degrades RAGAS faithfulness below 0.7, the Grafana dashboard shows it immediately.
