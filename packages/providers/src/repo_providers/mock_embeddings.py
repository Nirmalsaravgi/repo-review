"""Deterministic embeddings for tests and local runs without an API key.

Uses a bag-of-tokens scheme so overlapping words produce closer vectors than
unrelated text (good enough for ranking smoke tests).
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
from collections.abc import Sequence

from repo_providers.embeddings import EmbeddingProvider

DEFAULT_DIMS = 1024
_TOKEN = re.compile(r"[a-z0-9_]+", re.I)


def hash_embed_text(text: str, dimensions: int = DEFAULT_DIMS) -> list[float]:
    """Stable unit vector: hash each token into a few dimensions and accumulate."""
    acc = [0.0] * dimensions
    tokens = _TOKEN.findall(text.lower()) or ["_empty"]
    for tok in tokens:
        digest = hashlib.sha256(tok.encode()).digest()
        for i in range(0, 16, 4):
            (u,) = struct.unpack_from("<I", digest, i)
            idx = u % dimensions
            sign = 1.0 if (u & 1) else -1.0
            acc[idx] += sign
        # bigram with next token for a bit more structure
    for a, b in zip(tokens, tokens[1:], strict=False):
        digest = hashlib.sha256(f"{a}#{b}".encode()).digest()
        (u,) = struct.unpack_from("<I", digest, 0)
        acc[u % dimensions] += 0.5

    norm = math.sqrt(sum(x * x for x in acc)) or 1.0
    return [x / norm for x in acc]


class MockEmbeddingProvider(EmbeddingProvider):
    def __init__(self, *, model: str = "mock-hash", dimensions: int = DEFAULT_DIMS) -> None:
        self.model = model
        self.dimensions = dimensions

    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: str = "document",
    ) -> list[list[float]]:
        _ = input_type
        return [hash_embed_text(t, self.dimensions) for t in texts]
