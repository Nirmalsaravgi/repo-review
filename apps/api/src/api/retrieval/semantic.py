"""Semantic channel: embed query → pgvector (or in-memory) nearest neighbors."""

from __future__ import annotations

from typing import Sequence
from uuid import UUID

from repo_providers.embeddings import EmbeddingProvider
from repo_providers.factory import get_embedding_provider
from sqlalchemy.ext.asyncio import AsyncSession

from api.retrieval.types import RetrievalHit
from worker.ingest.semantic import SemanticHit, rank_by_embedding, semantic_search


class SemanticRetriever:
    def __init__(
        self,
        *,
        db: AsyncSession | None = None,
        repo_id: UUID | None = None,
        embedder: EmbeddingProvider | None = None,
        memory_docs: Sequence[tuple[RetrievalHit, Sequence[float]]] | None = None,
    ) -> None:
        self.db = db
        self.repo_id = repo_id
        self.embedder = embedder
        self.memory_docs = list(memory_docs or [])

    async def retrieve(self, query: str, *, limit: int = 20) -> list[RetrievalHit]:
        q = query.strip()
        if not q:
            return []
        embedder = self.embedder or get_embedding_provider()
        vectors = await embedder.embed([q], input_type="query")
        if not vectors:
            return []
        qvec = vectors[0]

        if self.memory_docs:
            ranked = rank_by_embedding(
                qvec,
                [(hit, vec) for hit, vec in self.memory_docs],
                limit=limit,
            )
            out: list[RetrievalHit] = []
            for hit, dist in ranked:
                out.append(
                    RetrievalHit(
                        path=hit.path,
                        start_line=hit.start_line,
                        end_line=hit.end_line,
                        snippet=hit.snippet,
                        score=1.0 - dist,
                        sources=("semantic",),
                        symbol_name=hit.symbol_name,
                        chunk_id=hit.chunk_id,
                        meta={"distance": f"{dist:.4f}"},
                    )
                )
            return out

        if self.db is None or self.repo_id is None:
            return []

        hits: list[SemanticHit] = await semantic_search(
            self.db, repo_id=self.repo_id, query_embedding=qvec, limit=limit
        )
        return [
            RetrievalHit(
                path=h.file_path.replace("\\", "/"),
                start_line=h.start_line,
                end_line=h.end_line,
                snippet=_snippet(h.header, h.content),
                score=1.0 - h.distance,
                sources=("semantic",),
                chunk_id=str(h.chunk_id),
                meta={"distance": f"{h.distance:.4f}"},
            )
            for h in hits
        ]


def _snippet(header: str, content: str, *, max_chars: int = 500) -> str:
    body = content.strip()
    if len(body) > max_chars:
        body = body[: max_chars - 1] + "…"
    # Prefer body; header is metadata for the embedder.
    return body or header[:max_chars]
