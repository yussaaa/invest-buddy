"""Embedding generator — configurable: local sentence-transformers or OpenAI."""

from __future__ import annotations

import asyncio
from functools import lru_cache

import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

# BGE models benefit from a query instruction prefix
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder:
    """Generates embeddings with lazy model loading."""

    def __init__(self):
        self._model = None
        self._settings = get_settings()

    def _load_local_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            log.info("embedder_loading", model=self._settings.embedding_model)
            self._model = SentenceTransformer(self._settings.embedding_model)
            log.info("embedder_loaded", model=self._settings.embedding_model)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts (for document ingestion)."""
        if not texts:
            return []

        if self._settings.embedding_provider == "openai":
            return await self._embed_openai(texts)
        else:
            return await self._embed_local(texts)

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query (adds instruction prefix for BGE models)."""
        model_name = self._settings.embedding_model.lower()
        if "bge" in model_name:
            query = BGE_QUERY_PREFIX + query

        results = await self.embed_texts([query])
        return results[0] if results else []

    async def _embed_local(self, texts: list[str]) -> list[list[float]]:
        """Embed using local sentence-transformers model."""
        def _encode():
            self._load_local_model()
            embeddings = self._model.encode(
                texts,
                batch_size=32,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            return [emb.tolist() for emb in embeddings]

        return await asyncio.to_thread(_encode)

    async def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        """Embed using OpenAI API."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._settings.openai_api_key)
        response = await client.embeddings.create(
            model=self._settings.embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """Return the singleton Embedder instance."""
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
