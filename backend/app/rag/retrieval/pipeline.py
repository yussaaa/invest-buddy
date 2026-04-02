"""RAG retrieval pipeline — 3-stage: decompose, retrieve, rerank.

Stage 1: Query decomposition (fast model → 3 sub-queries)
Stage 2: Multi-vector retrieval (dense pgvector + sparse BM25 + RRF)
Stage 3: Cross-encoder reranking (bge-reranker-base)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from sqlalchemy import text

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.rag.ingestion.embedder import get_embedder
from app.rag.retrieval.fusion import reciprocal_rank_fusion

log = structlog.get_logger(__name__)


class RAGPipeline:
    """3-stage retrieval pipeline using pgvector."""

    def __init__(self):
        self._reranker = None
        self._ready = False

    async def initialise(self) -> None:
        """Check pgvector is available. Reranker is lazy-loaded on first use."""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(text("SELECT 1"))
                result.scalar()
            self._ready = True
            log.info("rag_pipeline_ready")
        except Exception as e:
            log.warning("rag_pipeline_not_ready", error=str(e))
            self._ready = False

    async def retrieve(
        self,
        query: str,
        ticker: str,
        top_k: int | None = None,
    ) -> list[dict]:
        """Full 3-stage retrieval pipeline.

        Returns list of dicts with keys: id, ticker, document_type,
        source_url, published_date, text, entities, freshness_score,
        similarity, rrf_score.
        """
        if not self._ready:
            await self.initialise()
            if not self._ready:
                return []

        settings = get_settings()
        top_k = top_k or settings.rag_top_k

        try:
            # Stage 1: Decompose query
            sub_queries = await self._decompose_query(query, ticker)
            log.info("rag_stage1_decomposed", sub_queries=len(sub_queries))

            # Stage 2: Multi-vector retrieval
            candidates = await self._retrieve_candidates(sub_queries, ticker, top_k * 3)
            log.info("rag_stage2_candidates", count=len(candidates))

            if not candidates:
                return []

            # Stage 3: Rerank
            reranked = await self._rerank(query, candidates, top_k)
            log.info("rag_stage3_reranked", count=len(reranked))

            return reranked

        except Exception as e:
            log.error("rag_retrieve_error", error=str(e))
            return []

    # ── Stage 1: Query Decomposition ─────────────────────────────────────

    async def _decompose_query(self, query: str, ticker: str) -> list[str]:
        """Use fast model to break query into atomic sub-queries."""
        from app.models.factory import get_provider

        settings = get_settings()
        provider = get_provider()

        try:
            resp = await provider.complete(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Break the user's financial analysis question into exactly 3 "
                            "specific sub-queries for searching a document database. "
                            "Each sub-query should target a different information need. "
                            'Output JSON: {"sub_queries": ["query1", "query2", "query3"]}'
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Main query: {query}\nTicker: {ticker}",
                    },
                ],
                model=settings.fast_model,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=256,
            )
            data = json.loads(resp.content)
            subs = data.get("sub_queries", [query])
            return subs if subs else [query]
        except Exception as e:
            log.warning("rag_decompose_fallback", error=str(e))
            return [query]

    # ── Stage 2: Multi-Vector Retrieval ──────────────────────────────────

    async def _retrieve_candidates(
        self, sub_queries: list[str], ticker: str, top_k: int
    ) -> list[dict]:
        """Dense (pgvector) + Sparse (BM25) retrieval with RRF fusion."""

        # Run dense and sparse in parallel
        dense_task = self._dense_search(sub_queries, ticker, top_k)
        sparse_task = self._sparse_search(sub_queries, ticker, top_k)

        dense_results, sparse_results = await asyncio.gather(
            dense_task, sparse_task, return_exceptions=True
        )

        ranked_lists: list[list[dict]] = []

        if isinstance(dense_results, list) and dense_results:
            ranked_lists.append(dense_results)
        elif isinstance(dense_results, Exception):
            log.warning("rag_dense_error", error=str(dense_results))

        if isinstance(sparse_results, list) and sparse_results:
            ranked_lists.append(sparse_results)
        elif isinstance(sparse_results, Exception):
            log.warning("rag_sparse_error", error=str(sparse_results))

        if not ranked_lists:
            return []

        # Reciprocal Rank Fusion
        fused = reciprocal_rank_fusion(ranked_lists)
        return fused[:top_k]

    async def _dense_search(
        self, sub_queries: list[str], ticker: str, top_k: int
    ) -> list[dict]:
        """Embed sub-queries and search pgvector with ticker filter."""
        embedder = get_embedder()
        all_results: list[dict] = []

        for sq in sub_queries:
            query_embedding = await embedder.embed_query(sq)
            if not query_embedding:
                continue

            embedding_str = "[" + ",".join(f"{v:.6f}" for v in query_embedding) + "]"

            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("""
                        SELECT id, ticker, document_type, source_url, published_date,
                               text, entities, freshness_score,
                               1 - (embedding <=> :embedding::vector) AS similarity
                        FROM document_chunks
                        WHERE ticker = :ticker
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> :embedding::vector
                        LIMIT :limit
                    """),
                    {
                        "embedding": embedding_str,
                        "ticker": ticker.upper(),
                        "limit": top_k // len(sub_queries) + 1,
                    },
                )
                rows = result.fetchall()
                for row in rows:
                    all_results.append({
                        "id": row.id,
                        "ticker": row.ticker,
                        "document_type": row.document_type,
                        "source_url": row.source_url,
                        "published_date": row.published_date,
                        "text": row.text,
                        "entities": row.entities,
                        "freshness_score": row.freshness_score,
                        "similarity": float(row.similarity) if row.similarity else 0.0,
                    })

        # Deduplicate by id, keep highest similarity
        seen: dict[str, dict] = {}
        for r in all_results:
            rid = r["id"]
            if rid not in seen or r["similarity"] > seen[rid]["similarity"]:
                seen[rid] = r

        return sorted(seen.values(), key=lambda x: x["similarity"], reverse=True)

    async def _sparse_search(
        self, sub_queries: list[str], ticker: str, top_k: int
    ) -> list[dict]:
        """BM25 sparse retrieval over chunk texts for the ticker."""
        from rank_bm25 import BM25Okapi

        # Load all chunks for this ticker
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("""
                    SELECT id, ticker, document_type, source_url, published_date,
                           text, entities, freshness_score
                    FROM document_chunks
                    WHERE ticker = :ticker
                """),
                {"ticker": ticker.upper()},
            )
            rows = result.fetchall()

        if not rows:
            return []

        # Build BM25 index
        corpus = [row.text.lower().split() for row in rows]
        bm25 = BM25Okapi(corpus)

        # Search each sub-query and collect scores
        doc_scores: dict[str, float] = {}
        doc_map: dict[str, dict] = {}

        for sq in sub_queries:
            tokens = sq.lower().split()
            scores = bm25.get_scores(tokens)

            for idx, score in enumerate(scores):
                row = rows[idx]
                rid = row.id
                if rid not in doc_scores or score > doc_scores[rid]:
                    doc_scores[rid] = float(score)
                    doc_map[rid] = {
                        "id": row.id,
                        "ticker": row.ticker,
                        "document_type": row.document_type,
                        "source_url": row.source_url,
                        "published_date": row.published_date,
                        "text": row.text,
                        "entities": row.entities,
                        "freshness_score": row.freshness_score,
                        "bm25_score": float(score),
                    }

        # Sort by BM25 score descending
        sorted_ids = sorted(doc_scores.keys(), key=lambda x: doc_scores[x], reverse=True)
        return [doc_map[rid] for rid in sorted_ids[:top_k]]

    # ── Stage 3: Cross-Encoder Reranking ─────────────────────────────────

    async def _rerank(
        self, query: str, candidates: list[dict], top_k: int
    ) -> list[dict]:
        """Rerank candidates using a cross-encoder model."""
        if len(candidates) <= top_k:
            return candidates

        settings = get_settings()

        try:
            def _score():
                if self._reranker is None:
                    from sentence_transformers import CrossEncoder
                    log.info("reranker_loading", model=settings.reranker_model)
                    self._reranker = CrossEncoder(settings.reranker_model)
                    log.info("reranker_loaded")

                pairs = [(query, c["text"]) for c in candidates]
                scores = self._reranker.predict(pairs)
                return scores

            scores = await asyncio.to_thread(_score)

            # Attach scores and sort
            for i, candidate in enumerate(candidates):
                candidate["rerank_score"] = float(scores[i])

            reranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
            return reranked[:top_k]

        except Exception as e:
            log.warning("reranker_error_fallback", error=str(e))
            return candidates[:top_k]


# ── Singleton ────────────────────────────────────────────────────────────────

_pipeline: RAGPipeline | None = None


def get_rag_pipeline() -> RAGPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = RAGPipeline()
    return _pipeline
