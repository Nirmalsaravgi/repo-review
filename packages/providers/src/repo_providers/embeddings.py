"""Embedding provider interface (Phase 2 P3)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence


class EmbeddingProvider(ABC):
    """Batched text → vectors. Document vs query input types for asymmetric models."""

    model: str
    dimensions: int

    @abstractmethod
    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: str = "document",
    ) -> list[list[float]]:
        """Return one embedding per input text, same order."""
        raise NotImplementedError
