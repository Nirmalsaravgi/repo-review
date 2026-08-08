"""Hybrid retriever: run lexical + semantic, fuse with RRF."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

from repo_providers.embeddings import EmbeddingProvider
from sqlalchemy.ext.asyncio import AsyncSession

from api.retrieval.lexical import LexicalRetriever, SymbolRow
from api.retrieval.rrf import reciprocal_rank_fusion
from api.retrieval.semantic import SemanticRetriever
from api.retrieval.types import RetrievalHit


class HybridRetriever:
    def __init__(
        self,
        root: Path,
        *,
        db: AsyncSession | None = None,
        repo_id: UUID | None = None,
        embedder: EmbeddingProvider | None = None,
        lexical: LexicalRetriever | None = None,
        semantic: SemanticRetriever | None = None,
        rrf_k: int = 60,
    ) -> None:
        self.root = root
        self.rrf_k = rrf_k
        self.lexical = lexical or LexicalRetriever(root, db=db, repo_id=repo_id)
        self.semantic = semantic or SemanticRetriever(
            db=db, repo_id=repo_id, embedder=embedder
        )

    async def retrieve(self, query: str, *, limit: int = 10) -> list[RetrievalHit]:
        """Return fused hits. Empty channels are skipped rather than failing the merge."""
        # Fetch wider lists so RRF has room to promote mid-ranked consensus items.
        channel_limit = max(limit * 3, 15)
        lexical_hits, semantic_hits = await asyncio.gather(
            self.lexical.retrieve(query, limit=channel_limit),
            self.semantic.retrieve(query, limit=channel_limit),
        )
        lists = [lst for lst in (lexical_hits, semantic_hits) if lst]
        if not lists:
            return []
        if len(lists) == 1:
            # Still stamp scores for a stable interface.
            return [
                RetrievalHit(
                    path=h.path,
                    start_line=h.start_line,
                    end_line=h.end_line,
                    snippet=h.snippet,
                    score=1.0 / (self.rrf_k + i),
                    sources=h.sources,
                    symbol_name=h.symbol_name,
                    chunk_id=h.chunk_id,
                    meta=dict(h.meta),
                )
                for i, h in enumerate(lists[0][:limit], start=1)
            ]
        return reciprocal_rank_fusion(lists, k=self.rrf_k, limit=limit)


def build_hybrid_for_tests(
    root: Path,
    *,
    symbols: list[SymbolRow],
    memory_docs: list[tuple[RetrievalHit, list[float]]],
    embedder: EmbeddingProvider,
    rrf_k: int = 60,
) -> HybridRetriever:
    """Wire memory-backed channels (no Postgres) for unit/golden tests."""
    lexical = LexicalRetriever(root, memory_symbols=symbols)
    semantic = SemanticRetriever(embedder=embedder, memory_docs=memory_docs)
    return HybridRetriever(
        root, lexical=lexical, semantic=semantic, rrf_k=rrf_k, embedder=embedder
    )
