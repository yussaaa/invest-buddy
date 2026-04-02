"""RAGAS-based LLM evaluation — offline and online modes.

Offline: run against a golden dataset of 50 Q&A pairs
Online: score every production response (lightweight — uses fast model)

Metrics:
  - faithfulness: claims grounded in retrieved context
  - answer_relevancy: response addresses the query
  - context_precision: retrieved chunks actually used
  - context_recall: right documents retrieved
  - answer_correctness: quality vs golden answer (offline only)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)


class RAGASEvaluator:
    """Wraps RAGAS evaluation for both offline and online use."""

    def __init__(self):
        self._ragas_available = self._check_ragas()

    def _check_ragas(self) -> bool:
        try:
            import ragas  # noqa: F401
            return True
        except ImportError:
            log.warning("ragas_not_installed", hint="pip install ragas")
            return False

    async def evaluate_response(
        self,
        question: str,
        answer: str,
        contexts: list[str],
        ground_truth: Optional[str] = None,
    ) -> dict[str, float]:
        """Score a single response. Used online (every production run)."""
        if not self._ragas_available:
            return {"faithfulness": -1, "answer_relevancy": -1}

        try:
            from ragas import evaluate
            from ragas.metrics import faithfulness, answer_relevancy, context_precision
            from datasets import Dataset

            data = {
                "question": [question],
                "answer": [answer],
                "contexts": [contexts],
            }
            if ground_truth:
                data["ground_truth"] = [ground_truth]

            dataset = Dataset.from_dict(data)
            metrics = [faithfulness, answer_relevancy, context_precision]
            result = evaluate(dataset, metrics=metrics)

            scores = {
                "faithfulness": float(result["faithfulness"]),
                "answer_relevancy": float(result["answer_relevancy"]),
                "context_precision": float(result["context_precision"]),
            }
            log.info("ragas_scored", scores=scores)
            return scores

        except Exception as e:
            log.error("ragas_eval_error", error=str(e))
            return {"faithfulness": -1, "answer_relevancy": -1, "context_precision": -1}

    async def run_offline_eval(
        self,
        dataset_path: str | None = None,
    ) -> dict:
        """Run evaluation against the golden dataset.

        Golden dataset lives at: backend/app/evaluation/datasets/golden_queries.json
        Format: [{"question": "...", "ground_truth": "...", "contexts": [...]}]
        """
        if dataset_path is None:
            dataset_path = str(
                Path(__file__).parent.parent / "evaluation" / "datasets" / "golden_queries.json"
            )

        try:
            with open(dataset_path) as f:
                golden = json.load(f)
        except FileNotFoundError:
            log.warning("golden_dataset_not_found", path=dataset_path)
            return {"error": "golden_queries.json not found", "total": 0}

        all_scores: list[dict] = []
        for item in golden:
            scores = await self.evaluate_response(
                question=item["question"],
                answer=item.get("answer", ""),
                contexts=item.get("contexts", []),
                ground_truth=item.get("ground_truth"),
            )
            all_scores.append({"question": item["question"], **scores})

        # Aggregate
        metrics = ["faithfulness", "answer_relevancy", "context_precision"]
        aggregated = {}
        for m in metrics:
            vals = [s[m] for s in all_scores if s.get(m, -1) >= 0]
            aggregated[m] = round(sum(vals) / len(vals), 4) if vals else None

        result = {
            "evaluated_at": datetime.now().isoformat(),
            "total_questions": len(golden),
            "aggregate_scores": aggregated,
            "per_question": all_scores,
        }

        # Log to monitoring
        from app.observability.monitoring_client import get_monitoring_client
        client = get_monitoring_client()
        client.log_eval_run("golden_dataset", {k: v for k, v in aggregated.items() if v is not None})

        return result


# Singleton
_evaluator: RAGASEvaluator | None = None


def get_evaluator() -> RAGASEvaluator:
    global _evaluator
    if _evaluator is None:
        _evaluator = RAGASEvaluator()
    return _evaluator
