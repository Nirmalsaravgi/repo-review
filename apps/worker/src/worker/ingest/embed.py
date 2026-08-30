"""Chunk + embed working-tree files into pgvector (Phase 2 P3)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from repo_core.models import EMBEDDING_DIMS, Chunk, FileRecord, Symbol
from repo_parsing.chunking import BuiltChunk, chunk_file_symbols
from repo_parsing.languages import DETECTED_EXTENSIONS
from repo_providers.embeddings import EmbeddingProvider
from repo_providers.factory import get_embedding_provider
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_EMBED_BATCH = 32


def _is_embeddable_path(path: str) -> bool:
    """History may register every touched path; only embed parseable sources."""
    lower = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if lower.endswith((".lock", ".min.js", ".min.css")):
        return False
    if lower in {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock"}:
        return False
    ext = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""
    return ext in DETECTED_EXTENSIONS


async def sync_and_embed_repo(
    db: AsyncSession,
    *,
    org_id: UUID,
    repo_id: UUID,
    clone_path: str,
    repo_full_name: str,
    changed_file_ids: set[UUID] | None = None,
    embedder: EmbeddingProvider | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build AST chunks and embeddings. Skips files whose chunks are already current.

    A file is (re)embedded when it is in `changed_file_ids`, or it has zero chunks.
    `force=True` re-embeds every embeddable file regardless of the content-SHA cache
    — required when switching embedding providers (e.g. mock → voyage), since the
    content SHA is over chunk text, not the embedding model, so a normal run would
    keep the stale vectors.
    """
    embedder = embedder or get_embedding_provider()
    root = Path(clone_path)

    files_result = await db.execute(
        select(FileRecord).where(FileRecord.repo_id == repo_id, FileRecord.is_deleted.is_(False))
    )
    files = list(files_result.scalars().all())

    # Files that already have at least one chunk
    counted = await db.execute(
        select(Chunk.file_id, func.count())
        .where(Chunk.repo_id == repo_id)
        .group_by(Chunk.file_id)
    )
    chunk_counts = dict(counted.all())

    files_embedded = 0
    files_skipped = 0
    chunks_written = 0
    chunks_reused = 0
    errors = 0

    pending: list[tuple[FileRecord, list[BuiltChunk]]] = []

    for rec in files:
        if not _is_embeddable_path(rec.path):
            files_skipped += 1
            continue

        has_chunks = chunk_counts.get(rec.id, 0) > 0
        must = (
            force
            or (changed_file_ids is not None and rec.id in changed_file_ids)
            or not has_chunks
        )
        if not must:
            files_skipped += 1
            continue

        abs_path = root / rec.path
        try:
            source = abs_path.read_bytes()
        except OSError:
            errors += 1
            continue

        # Binary / corrupt sources: NUL is valid UTF-8 but illegal in Postgres text.
        if b"\x00" in source:
            logger.info("skip binary/NUL content for chunking: %s", rec.path)
            files_skipped += 1
            continue

        sym_result = await db.execute(select(Symbol).where(Symbol.file_id == rec.id))
        symbols = list(sym_result.scalars().all())
        try:
            built = chunk_file_symbols(
                repo_full_name=repo_full_name,
                path=rec.path,
                language=rec.language,
                source=source,
                symbols=symbols,
            )
        except Exception:
            logger.exception("chunk failed for %s", rec.path)
            errors += 1
            continue

        if not built:
            await db.execute(delete(Chunk).where(Chunk.file_id == rec.id))
            files_embedded += 1
            continue

        # content_sha skip: if existing set matches exactly, keep embeddings.
        existing = await db.execute(select(Chunk.content_sha).where(Chunk.file_id == rec.id))
        existing_shas = set(existing.scalars().all())
        new_shas = {c.content_sha for c in built}
        if not force and existing_shas == new_shas and has_chunks:
            chunks_reused += len(built)
            files_skipped += 1
            continue

        await db.execute(delete(Chunk).where(Chunk.file_id == rec.id))
        pending.append((rec, built))

    # Persist rows then embed in batches
    to_embed: list[tuple[Chunk, str]] = []
    for rec, built in pending:
        for ch in built:
            row = Chunk(
                id=uuid4(),
                org_id=org_id,
                repo_id=repo_id,
                file_id=rec.id,
                symbol_id=ch.symbol_id,
                start_line=ch.start_line,
                end_line=ch.end_line,
                header=ch.header,
                content=ch.content,
                content_sha=ch.content_sha,
                embedding=None,
            )
            db.add(row)
            to_embed.append((row, ch.embed_text))
        files_embedded += 1
        chunks_written += len(built)

    await db.flush()

    for start in range(0, len(to_embed), _EMBED_BATCH):
        batch = to_embed[start : start + _EMBED_BATCH]
        texts = [t for _, t in batch]
        try:
            vectors = await embedder.embed(texts, input_type="document")
        except Exception:
            logger.exception("embed batch failed at offset %s", start)
            errors += 1
            continue
        for (row, _), vec in zip(batch, vectors, strict=True):
            if len(vec) != embedder.dimensions and embedder.dimensions == EMBEDDING_DIMS:
                # Allow mock dims mismatch only if configured otherwise; truncate/pad.
                if len(vec) > EMBEDDING_DIMS:
                    vec = vec[:EMBEDDING_DIMS]
                else:
                    vec = list(vec) + [0.0] * (EMBEDDING_DIMS - len(vec))
            row.embedding = vec

    await db.flush()
    stats = {
        "files_embedded": files_embedded,
        "files_skipped": files_skipped,
        "chunks_written": chunks_written,
        "chunks_reused": chunks_reused,
        "embed_model": embedder.model,
        "errors": errors,
    }
    logger.info("index_code embed for repo %s: %s", repo_id, stats)
    return stats
