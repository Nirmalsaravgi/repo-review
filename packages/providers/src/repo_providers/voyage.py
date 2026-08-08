"""Voyage AI embedding provider (voyage-code-3) via httpx."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import httpx

from repo_providers.base import ProviderError
from repo_providers.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

_API_URL = "https://api.voyageai.com/v1/embeddings"
_DEFAULT_MODEL = "voyage-code-3"
_DEFAULT_DIMS = 1024
_MAX_BATCH = 64


class VoyageEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        dimensions: int = _DEFAULT_DIMS,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ProviderError("Voyage embedding API key is empty")
        self.api_key = api_key
        self.model = model or _DEFAULT_MODEL
        self.dimensions = dimensions
        self.max_retries = max_retries
        self.timeout = timeout

    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: str = "document",
    ) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for start in range(0, len(texts), _MAX_BATCH):
            batch = list(texts[start : start + _MAX_BATCH])
            out.extend(await self._embed_batch(batch, input_type=input_type))
        return out

    async def _embed_batch(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        payload = {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": self.dimensions,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_err: Exception | None = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    resp = await client.post(_API_URL, json=payload, headers=headers)
                    if resp.status_code in {429, 500, 502, 503, 504}:
                        raise ProviderError(f"Voyage HTTP {resp.status_code}: {resp.text[:200]}")
                    resp.raise_for_status()
                    data = resp.json()
                    items = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
                    vectors = [list(map(float, item["embedding"])) for item in items]
                    if len(vectors) != len(texts):
                        raise ProviderError(
                            f"Voyage returned {len(vectors)} vectors for {len(texts)} texts"
                        )
                    return vectors
                except (httpx.HTTPError, ProviderError, KeyError, TypeError, ValueError) as exc:
                    last_err = exc
                    if attempt >= self.max_retries:
                        break
                    delay = 0.5 * (2**attempt)
                    logger.warning("Voyage embed retry %s after %s: %s", attempt + 1, delay, exc)
                    await asyncio.sleep(delay)
        raise ProviderError(f"Voyage embed failed: {last_err}")
