"""RAG retrieval pipeline — 3-stage: decompose → retrieve → rerank.

Phase 1 stub: returns empty context so agents work without Qdrant.
Phase 3 will implement:
  Stage 1: Query decomposition (Haiku breaks query into sub-queries)
  Stage 2: Multi-vector retrieval (dense Qdrant + sparse BM25 + entity graph)
  Stage 3: Cross-encoder reranking + context assembly
"""

from __future__ import annotations

from typing import Optional

import structlog

log = structlog.get_logger(__name__)


class RAGPipeline:
    """3-stage retrieval pipeline (Phase 3 implementation)."""

    def __init__(self):
        self._qdrant = None
        self._embedder = None
        self._reranker = None
        self._ready = False

    async def initialise(self) -> None:
        """Connect to Qdrant and load models (called on startup in Phase 3)."""
        try:
            from qdrant_client import QdrantClient
            from app.config import get_settings
            settings = get_settings()
            self._qdrant = QdrantClient(url=settings.qdrant_url)
            self._ready = True
            log.info("rag_pipeline_ready")
        except Exception as e:
            log.warning("rag_pipeline_not_ready", error=str(e))
            self._ready = False

    async def retrieve(
        self,
        query: str,
        ticker: str,
        top_k: int = 8,
        date_filter_days: int = 365,
    ) -> list[dict]:
        """Retrieve relevant documents for a query and ticker.

        Returns empty list if Qdrant is not available (Phase 1 behaviour).
        """
        if not self._ready:
            return []

        try:
            # Stage 1: Decompose query into sub-queries
            sub_queries = await self._decompose_query(query, ticker)

            # Stage 2: Multi-vector retrieval
            candidates = await self._retrieve_candidates(sub_queries, ticker, top_k * 3)

            # Stage 3: Rerank
            reranked = await self._rerank(query, candidates, top_k)

            return reranked
        except Exception as e:
            log.error("rag_retrieve_error", error=str(e))
            return []

    async def _decompose_query(self, query: str, ticker: str) -> list[str]:
        """Use fast model to break query into atomic sub-queries."""
        from app.models.factory import get_provider
        from app.config import get_settings
        import json

        settings = get_settings()
        provider = get_provider()

        try:
            resp = await provider.complete(
                messages=[
                    {"role": "system", "content": "Break the user's question into 3 specific sub-queries for a financial document search. Output JSON: {\"sub_queries\": [\"...\", \"...\", \"...\"]}"},
                    {"role": "user", "content": f"Main query: {query}\nTicker: {ticker}"},
                ],
                model=settings.fast_model,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=256,
            )
            data = json.loads(resp.content)
            return data.get("sub_queries", [query])
        except Exception:
            return [query]

    async def _retrieve_candidates(self, sub_queries: list[str], ticker: str, top_k: int) -> list[dict]:
        """Dense retrieval from Qdrant with ticker filter."""
        # Phase 3 implementation: embed sub_queries, search Qdrant
        return []

    async def _rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """Cross-encoder reranking of candidates."""
        # Phase 3 implementation: score with bge-reranker-base
        return candidates[:top_k]


# Singleton
_pipeline: RAGPipeline | None = None


def get_rag_pipeline() -> RAGPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = RAGPipeline()
    return _pipeline
