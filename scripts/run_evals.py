#!/usr/bin/env python3
"""Offline evaluation runner — scores the system against the golden dataset.

Usage:
    python scripts/run_evals.py
    python scripts/run_evals.py --ticker AAPL
    python scripts/run_evals.py --dataset path/to/custom_dataset.json

Outputs a score card to stdout and logs results to MLflow/W&B.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure backend is on the path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.agents.orchestrator.graph import run_analysis
from app.observability.ragas_evaluator import get_evaluator
from app.observability.monitoring_client import get_monitoring_client


async def run_single_eval(item: dict) -> dict:
    """Run one golden query through the pipeline and score it."""
    ticker = item.get("ticker", "AAPL")
    question = item["question"]

    print(f"  Running: [{ticker}] {question[:60]}...")

    try:
        state = await run_analysis(query=question, ticker=ticker)
        report = state.get("final_report")
        answer = report.detailed_analysis if report else ""
        contexts = [
            doc.get("text", "") for doc in state.get("retrieved_documents", [])
        ][:5]

        evaluator = get_evaluator()
        scores = await evaluator.evaluate_response(
            question=question,
            answer=answer,
            contexts=contexts if contexts else [answer[:500]],
            ground_truth=item.get("ground_truth"),
        )
        return {
            "question": question,
            "ticker": ticker,
            "status": "success",
            "answer_length": len(answer),
            "agents_used": state.get("required_agents", []),
            **scores,
        }
    except Exception as e:
        print(f"    ERROR: {e}")
        return {
            "question": question,
            "ticker": ticker,
            "status": "error",
            "error": str(e),
            "faithfulness": -1,
            "answer_relevancy": -1,
        }


async def main(dataset_path: str | None = None, ticker_filter: str | None = None):
    dataset_path = dataset_path or str(
        Path(__file__).parent.parent
        / "backend/app/evaluation/datasets/golden_queries.json"
    )

    with open(dataset_path) as f:
        golden = json.load(f)

    if ticker_filter:
        golden = [q for q in golden if q.get("ticker", "").upper() == ticker_filter.upper()]
        print(f"Filtered to {len(golden)} queries for {ticker_filter}")

    print(f"\n{'='*60}")
    print(f"Running evaluation — {len(golden)} queries")
    print(f"Dataset: {dataset_path}")
    print(f"{'='*60}\n")

    results = []
    for i, item in enumerate(golden, 1):
        print(f"[{i}/{len(golden)}]", end=" ")
        result = await run_single_eval(item)
        results.append(result)

    # Aggregate scores
    metrics = ["faithfulness", "answer_relevancy", "context_precision"]
    print(f"\n{'='*60}")
    print("EVALUATION RESULTS")
    print(f"{'='*60}")

    aggregated = {}
    for m in metrics:
        vals = [r[m] for r in results if r.get(m, -1) >= 0]
        avg = round(sum(vals) / len(vals), 4) if vals else None
        aggregated[m] = avg
        indicator = "✅" if (avg or 0) > 0.7 else "⚠️" if (avg or 0) > 0.5 else "❌"
        print(f"  {indicator} {m:25s}: {f'{avg:.4f}' if avg is not None else 'N/A'}")

    success = sum(1 for r in results if r["status"] == "success")
    print(f"\n  Total queries:  {len(results)}")
    print(f"  Successful:     {success}/{len(results)}")
    print(f"{'='*60}\n")

    # Log to monitoring
    client = get_monitoring_client()
    client.log_eval_run("offline_golden", {k: v for k, v in aggregated.items() if v is not None})

    # Save detailed results
    output_path = Path(__file__).parent / "eval_results.json"
    with open(output_path, "w") as f:
        json.dump({"aggregated": aggregated, "per_query": results}, f, indent=2, default=str)
    print(f"Detailed results saved to: {output_path}")

    return aggregated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run offline evaluation")
    parser.add_argument("--dataset", help="Path to golden dataset JSON")
    parser.add_argument("--ticker", help="Filter to a specific ticker")
    args = parser.parse_args()

    asyncio.run(main(dataset_path=args.dataset, ticker_filter=args.ticker))
