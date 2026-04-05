# Evaluation Framework

This document describes how Agent Invest measures quality, detects regressions, and ensures safety. The system uses three evaluation layers: online scoring (every production run), offline evaluation (golden dataset), and guardrails (real-time safety checks).

## Three-Layer Evaluation Architecture

```
Layer 1: GUARDRAILS (real-time, every run)
  → Hallucination detection (LLM-as-judge)
  → Investment advice detection (regex, 17 patterns)
  → Citation completeness check
  → Confidence threshold check
  ↓ result: guardrail_flags[] in AgentState

Layer 2: ONLINE EVAL (async, every run)
  → RAGAS scoring (faithfulness, relevancy, precision)
  → Logged to MLflow + Prometheus
  ↓ result: eval_metadata in persistence_node

Layer 3: OFFLINE EVAL (on-demand, golden dataset)
  → 10 curated queries with reference answers
  → Full pipeline run + RAGAS scoring per query
  → Aggregate score card logged to MLflow
  ↓ result: scripts/run_evals.py output
```

## Layer 1: Guardrails

The guardrails node runs after all 5 agents complete and before synthesis. It checks every `AgentResult`:

### Hallucination Detection (LLM-as-Judge)

For each agent's findings, an LLM judge evaluates:

```
System: You are a financial fact-checker.
Given the retrieved documents and the agent's findings,
identify any claim that cannot be verified in the evidence.

Output: {"grounded_claims": [...], "ungrounded_claims": [...], "hallucination_score": 0.0-1.0}
```

- **Score 0.0-0.3**: Findings well-grounded — pass
- **Score 0.3-0.7**: Some unverified claims — warning flag raised
- **Score 0.7-1.0**: Significant hallucination — error flag raised

The judge uses the **fast model** (gpt-4o-mini) to minimize cost — it's a binary classification task.

### Investment Advice Detection (Regex)

17 compiled regex patterns catch phrases like:
- "you should buy/sell"
- "strong buy/sell"
- "will definitely"
- "price target of $X"
- "guaranteed returns"
- "I recommend"

Each match is flagged with a context snippet (±20 chars) for audit. The synthesizer is instructed to use hedged language ("analysts suggest", "historically associated with") instead.

### Additional Checks

| Check | Trigger | Severity |
|-------|---------|----------|
| Low confidence | `agent.confidence < 0.3` | Warning |
| Missing citations | High confidence but zero citations | Warning |
| Data unavailable | Zero key data points returned | Warning |

### Guardrail Metrics

All flags are emitted as Prometheus counters:
```
guardrail_triggers_total{flag_type="hallucination", severity="warning"} 3
guardrail_triggers_total{flag_type="investment_advice", severity="warning"} 1
```

Visible in the Grafana dashboard under "Agent Performance" → "Guardrail Triggers".

## Layer 2: Online RAGAS Scoring

After every analysis run, the `persistence_node` scores the final report using RAGAS:

### Metrics

| Metric | What It Measures | Range | Target |
|--------|-----------------|-------|--------|
| **Faithfulness** | Are all claims grounded in retrieved documents? | 0-1 | >0.7 |
| **Answer Relevancy** | Does the response address the user's query? | 0-1 | >0.8 |
| **Context Precision** | Were retrieved chunks actually useful? | 0-1 | >0.6 |

### When RAGAS Scores Are Available

RAGAS requires **retrieved documents** (RAG context) to evaluate. Scores are only meaningful when:
- The "Include Document Research" toggle is ON
- Documents have been ingested for the ticker
- The RAG pipeline returns non-empty context

When RAG is OFF, RAGAS scores are 0.0 (not applicable — agents work from live tool data only, not retrieved documents).

### Where Scores Are Logged

1. **MLflow**: Per-run metrics (`ragas_faithfulness`, `ragas_answer_relevancy`, `ragas_context_precision`)
2. **Prometheus**: Gauge metrics updated after each run
3. **Grafana**: "Quality Metrics" row shows real-time RAGAS gauges

## Layer 3: Offline Evaluation (Golden Dataset)

### Purpose

The golden dataset provides a **regression test** for prompt changes. When you modify an agent's system prompt:
1. Bump `PROMPT_VERSION` in the agent's `prompts.py`
2. Run `python scripts/run_evals.py`
3. Compare aggregate scores in MLflow: prompt v1 vs v2

### Dataset Structure

Located at `backend/app/evaluation/datasets/golden_queries.json`:

```json
{
  "question": "What is Apple's current P/E ratio compared to tech sector average?",
  "ticker": "AAPL",
  "required_agents": ["fundamental"],
  "ground_truth": "Apple's P/E is around 30-35x. Tech sector average is ~25-30x."
}
```

Current: **10 queries** covering all 5 agent types.
Planned: Expand to **50 queries** with edge cases (delisted stocks, missing data, multi-ticker).

### Running Offline Eval

```bash
make eval

# Or with filters:
python scripts/run_evals.py --ticker AAPL
python scripts/run_evals.py --dataset path/to/custom_dataset.json
```

Output:
```
EVALUATION RESULTS
  ✅ faithfulness:              0.8200
  ✅ answer_relevancy:          0.8500
  ⚠️  context_precision:        0.6100

  Total queries:  10
  Successful:     10/10
```

Results are logged to MLflow experiment `offline_golden` for historical comparison.

## Prompt Versioning

Every agent's `prompts.py` file contains:

```python
PROMPT_VERSION = "v1"
```

This version string is:
1. Logged to MLflow as a parameter on every run
2. Used to correlate quality changes with prompt changes
3. Enables filtering: "show me all runs with `prompt_fundamental=v1` vs `v2`"

### Workflow for Prompt Changes

```
1. Edit agent's prompts.py → change system prompt
2. Bump PROMPT_VERSION to "v2"
3. Run: python scripts/run_evals.py
4. Open MLflow → compare v1 vs v2 aggregate scores
5. If v2 is worse → revert. If better → commit.
```

## Token & Cost Monitoring

Every LLM call emits token counts to Prometheus:

```
token_usage_total{model="gpt-4o", type="prompt"} 6695
token_usage_total{model="gpt-4o", type="completion"} 1395
estimated_cost_usd{model="gpt-4o"} 0.01088
```

Cost estimation uses a per-model pricing table in `observability/metrics.py`. One analysis run costs approximately:

| Component | Tokens | Cost |
|-----------|--------|------|
| gpt-4o-mini (classifier + fast agents) | ~350 | $0.0005 |
| gpt-4o (fundamental + risk + synthesis) | ~8,000 | $0.0109 |
| **Total** | **~8,350** | **$0.0114** |

The Grafana dashboard "Token Usage & Cost" row tracks cumulative spend.

## What's Not Yet Implemented (Roadmap)

- **Answer Correctness** scoring against golden dataset ground truths (requires RAGAS `answer_correctness` metric)
- **Golden dataset expansion** to 50 queries with edge cases
- **Automated regression alerts** — Grafana alert when faithfulness drops below 0.6
- **A/B testing framework** — run two prompt versions simultaneously and compare
- **User feedback integration** — use thumbs-up/down data to weight eval scores
