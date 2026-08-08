"""Nearest-neighbor helpers over chunk embeddings (Phase 2 P3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class SemanticHit:
    chunk_id: UUID
    file_path: str
    start_line: int
    end_line: int
    header: str
    content: str
    distance: float


async def semantic_search(
    db: AsyncSession,
    *,
    repo_id: UUID,
    query_embedding: Sequence[float],
    limit: int = 10,
) -> list[SemanticHit]:
    """Cosine-distance ANN via pgvector (`<=>`). Lower distance = closer."""
    # Bind as vector literal — asyncpg + pgvector accept list via cast.
    vec_literal = "[" + ",".join(str(float(x)) for x in query_embedding) + "]"
    stmt = text(
        """
        SELECT c.id, f.path, c.start_line, c.end_line, c.header, c.content,
               (c.embedding <=> CAST(:qvec AS vector)) AS distance
        FROM chunks c
        JOIN files f ON f.id = c.file_id
        WHERE c.repo_id = :repo_id AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> CAST(:qvec AS vector)
        LIMIT :lim
        """
    )
    result = await db.execute(
        stmt,
        {"repo_id": str(repo_id), "qvec": vec_literal, "lim": limit},
    )
    hits: list[SemanticHit] = []
    for row in result.all():
        hits.append(
            SemanticHit(
                chunk_id=row[0],
                file_path=row[1],
                start_line=row[2],
                end_line=row[3],
                header=row[4],
                content=row[5],
                distance=float(row[6]),
            )
        )
    return hits


def cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """1 - cosine similarity (for in-memory tests without Postgres)."""
    import math

    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return 1.0 - (dot / (na * nb))


def rank_by_embedding(
    query: Sequence[float],
    candidates: list[tuple[Any, Sequence[float]]],
    *,
    limit: int = 10,
) -> list[tuple[Any, float]]:
    scored = [(item, cosine_distance(query, vec)) for item, vec in candidates]
    scored.sort(key=lambda t: t[1])
    return scored[:limit]
