"""Phase 2 — sync working-tree files and extract symbols into Postgres."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from repo_core.models import Chunk, FileRecord, Symbol
from repo_parsing import DETECTED_EXTENSIONS, extract_symbols
from repo_parsing.languages import SKIP_DIR_NAMES, detect_language
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_BATCH_FLUSH = 50
FULL_REINDEX_PATH_THRESHOLD = 500


def iter_source_files(clone_path: str | Path) -> list[tuple[str, Path]]:
    """Return (repo-relative posix path, absolute Path) for parseable sources."""
    root = Path(clone_path)
    if not root.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        parts = _path_parts(rel)
        if any(p in SKIP_DIR_NAMES for p in parts[:-1]):
            continue
        if path.suffix.lower() not in DETECTED_EXTENSIONS:
            continue
        out.append((rel, path))
    return out


def _path_parts(rel: str) -> list[str]:
    return [p for p in rel.split("/") if p]


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(content, usedforsecurity=False).hexdigest()


async def sync_and_parse_repo(
    db: AsyncSession,
    *,
    org_id: UUID,
    repo_id: UUID,
    clone_path: str,
    only_paths: list[str] | None = None,
) -> tuple[dict[str, Any], set[UUID]]:
    """Upsert files from the working tree and replace symbols per changed file.

    When `only_paths` is set, only those paths are considered (plus deletions).
    Idempotent: unchanged blob_sha skips re-parse.
    Returns (stats, set of file ids that were reparsed or deleted).
    """
    existing_result = await db.execute(select(FileRecord).where(FileRecord.repo_id == repo_id))
    files_by_path = {f.path: f for f in existing_result.scalars().all()}

    root = Path(clone_path)
    if only_paths is None:
        discovered = iter_source_files(clone_path)
        removed_paths: list[str] = []
    else:
        discovered = []
        removed_paths = []
        for rel in only_paths:
            rel = rel.replace("\\", "/").lstrip("./")
            abs_path = root / rel
            if not abs_path.is_file():
                removed_paths.append(rel)
                continue
            if abs_path.suffix.lower() not in DETECTED_EXTENSIONS:
                continue
            parts = _path_parts(rel)
            if any(p in SKIP_DIR_NAMES for p in parts[:-1]):
                continue
            discovered.append((rel, abs_path))

    parsed = 0
    skipped_unchanged = 0
    symbols_upserted = 0
    errors = 0
    deleted = 0
    changed_ids: set[UUID] = set()

    for rel in removed_paths:
        rec = files_by_path.get(rel)
        if rec is None:
            continue
        await db.execute(delete(Symbol).where(Symbol.file_id == rec.id))
        await db.execute(delete(Chunk).where(Chunk.file_id == rec.id))
        rec.is_deleted = True
        rec.blob_sha = None
        changed_ids.add(rec.id)
        deleted += 1

    for i, (rel, abs_path) in enumerate(discovered):
        try:
            content = abs_path.read_bytes()
        except OSError:
            errors += 1
            continue

        sha = _blob_sha(content)
        lang = detect_language(rel)
        loc = content.count(b"\n") + (1 if content and not content.endswith(b"\n") else 0)

        rec = files_by_path.get(rel)
        if rec is None:
            rec = FileRecord(
                id=uuid4(),
                org_id=org_id,
                repo_id=repo_id,
                path=rel,
                blob_sha=sha,
                language=lang,
                loc=loc,
                is_deleted=False,
            )
            db.add(rec)
            files_by_path[rel] = rec
            await db.flush()
            need_parse = True
        else:
            rec.is_deleted = False
            rec.language = lang
            rec.loc = loc
            if rec.blob_sha == sha:
                skipped_unchanged += 1
                need_parse = False
            else:
                rec.blob_sha = sha
                need_parse = True

        if not need_parse:
            continue

        try:
            extracted = extract_symbols(rel, content, language=lang)
        except Exception:
            logger.exception("parse failed for %s", rel)
            errors += 1
            continue

        await db.execute(delete(Symbol).where(Symbol.file_id == rec.id))
        id_by_index: dict[int, UUID] = {}
        for idx, sym in enumerate(extracted):
            sym_id = uuid4()
            id_by_index[idx] = sym_id
            parent_id = id_by_index.get(sym.parent_index) if sym.parent_index is not None else None
            db.add(
                Symbol(
                    id=sym_id,
                    org_id=org_id,
                    repo_id=repo_id,
                    file_id=rec.id,
                    parent_symbol_id=parent_id,
                    name=sym.name[:512],
                    qualified_name=sym.qualified_name,
                    kind=sym.kind,
                    signature=sym.signature,
                    docstring=sym.docstring,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    start_byte=sym.start_byte,
                    end_byte=sym.end_byte,
                )
            )
        symbols_upserted += len(extracted)
        parsed += 1
        changed_ids.add(rec.id)

        if (i + 1) % _BATCH_FLUSH == 0:
            await db.flush()

    await db.flush()
    stats = {
        "files_discovered": len(discovered),
        "files_parsed": parsed,
        "files_skipped_unchanged": skipped_unchanged,
        "files_deleted": deleted,
        "symbols_upserted": symbols_upserted,
        "errors": errors,
        "incremental": only_paths is not None,
    }
    logger.info("index_code parse for repo %s: %s", repo_id, stats)
    return stats, changed_ids
