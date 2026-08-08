"""Reciprocal Rank Fusion."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from api.retrieval.types import RetrievalHit


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[RetrievalHit]],
    *,
    k: int = 60,
    limit: int = 20,
    key_fn: Callable[[RetrievalHit], str] | None = None,
) -> list[RetrievalHit]:
    """Fuse ranked lists with RRF: score += 1 / (k + rank).

    Hits with the same key are merged: best snippet kept, scores summed,
    sources unioned. Default key is file path (eval-friendly).
    """
    key_fn = key_fn or (lambda h: h.file_key)
    scores: dict[str, float] = {}
    best: dict[str, RetrievalHit] = {}
    sources: dict[str, set[str]] = {}

    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked, start=1):
            key = key_fn(hit)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            sources.setdefault(key, set()).update(hit.sources)
            prev = best.get(key)
            if prev is None or len(hit.snippet) > len(prev.snippet):
                best[key] = hit

    fused: list[RetrievalHit] = []
    for key, score in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
        hit = best[key]
        fused.append(
            RetrievalHit(
                path=hit.path,
                start_line=hit.start_line,
                end_line=hit.end_line,
                snippet=hit.snippet,
                score=score,
                sources=tuple(sorted(sources.get(key, set()) or hit.sources)),
                symbol_name=hit.symbol_name,
                chunk_id=hit.chunk_id,
                meta=dict(hit.meta),
            )
        )
        if len(fused) >= limit:
            break
    return fused
