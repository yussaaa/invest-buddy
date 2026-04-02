"""Reciprocal Rank Fusion — merges multiple ranked result lists."""

from __future__ import annotations


def reciprocal_rank_fusion(
    ranked_lists: list[list[dict]],
    k: int = 60,
    id_key: str = "id",
) -> list[dict]:
    """Merge multiple ranked result lists using RRF scoring.

    For each document, score = sum(1 / (k + rank_i)) across all lists
    where the document appears. Higher score = more consistently ranked
    high across different retrieval methods.

    Args:
        ranked_lists: List of ranked result lists (each is a list of dicts with 'id')
        k: RRF constant (default 60 — standard in literature)
        id_key: Key to use for deduplication

    Returns:
        Merged list sorted by RRF score descending.
    """
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    for ranked_list in ranked_lists:
        for rank, doc in enumerate(ranked_list):
            doc_id = doc.get(id_key, str(rank))
            rrf_score = 1.0 / (k + rank + 1)  # rank is 0-indexed, so +1
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf_score

            # Keep the first occurrence of each document (highest-detail version)
            if doc_id not in docs:
                docs[doc_id] = doc

    # Sort by RRF score descending
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    result = []
    for doc_id in sorted_ids:
        doc = docs[doc_id].copy()
        doc["rrf_score"] = round(scores[doc_id], 6)
        result.append(doc)

    return result
