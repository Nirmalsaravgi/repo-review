"""Published wiki pages as a retrieval channel beside code search."""

from __future__ import annotations

import re
from uuid import UUID

from repo_core.models import WikiArtifact
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.retrieval.types import RetrievalHit

_TOKEN = re.compile(r"[a-z0-9]{3,}")


def rank_pages(pages: list[dict[str, str]], query: str, *, limit: int = 8) -> list[dict[str, str]]:
    tokens = set(_TOKEN.findall((query or "").lower()))
    if not tokens:
        return []
    scored: list[tuple[float, dict[str, str]]] = []
    for page in pages:
        hay = f"{page.get('title', '')} {page.get('body', '')}".lower()
        words = set(_TOKEN.findall(hay))
        overlap = tokens & words
        if not overlap:
            continue
        scored.append((len(overlap) / len(tokens), page))
    scored.sort(key=lambda item: (-item[0], item[1].get("slug", "")))
    return [page for _score, page in scored[:limit]]


class WikiRetriever:
    def __init__(self, db: AsyncSession, repo_id: UUID) -> None:
        self.db = db
        self.repo_id = repo_id

    async def retrieve(self, query: str, *, limit: int = 8) -> list[RetrievalHit]:
        rows = (
            await self.db.execute(
                select(WikiArtifact).where(
                    WikiArtifact.repo_id == self.repo_id,
                    WikiArtifact.status == "published",
                )
            )
        ).scalars().all()
        pages = [{"slug": row.slug, "title": row.title, "body": row.body or ""} for row in rows]
        ranked = rank_pages(pages, query, limit=limit)
        hits: list[RetrievalHit] = []
        by_slug = {row.slug: row for row in rows}
        for index, page in enumerate(ranked, start=1):
            row = by_slug[page["slug"]]
            path = _first_file(row.depends_on) or page["slug"]
            hits.append(
                RetrievalHit(
                    path=path,
                    start_line=1,
                    end_line=1,
                    snippet=(row.body or "")[:500],
                    score=1.0 / (60 + index),
                    sources=("wiki",),
                    symbol_name=row.title,
                    meta={"slug": row.slug, "type": row.type},
                )
            )
        return hits


def _first_file(depends_on: list[str] | None) -> str | None:
    for ref in depends_on or []:
        if ref.startswith("file:"):
            return ref[5:]
    return None
