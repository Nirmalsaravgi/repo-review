"""Shared retrieval hit type."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    path: str
    start_line: int
    end_line: int
    snippet: str
    score: float = 0.0
    sources: tuple[str, ...] = ()
    symbol_name: str | None = None
    chunk_id: str | None = None
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def file_key(self) -> str:
        """File-level fusion key (matches eval recall@k on paths)."""
        return self.path.replace("\\", "/")
