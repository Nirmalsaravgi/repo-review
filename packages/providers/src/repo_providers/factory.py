"""Select an `LLMProvider` from configuration.

`build_llm_provider` takes explicit strings and has no dependency on the core
package, so it's trivially unit-testable. `get_llm_provider` is the wired
convenience that reads `Settings`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repo_providers.base import LLMProvider, ProviderError
from repo_providers.embeddings import EmbeddingProvider

if TYPE_CHECKING:  # pragma: no cover
    from repo_core.config import Settings


def build_llm_provider(
    *, provider: str, api_key: str, model: str, min_request_interval: float = 0.0
) -> LLMProvider:
    name = (provider or "").strip().lower()
    if name == "gemini":
        from repo_providers.gemini import GeminiProvider

        return GeminiProvider(
            api_key=api_key, model=model, min_request_interval=min_request_interval
        )
    if name in {"mock", "fake"}:
        from repo_providers.mock import MockProvider

        return MockProvider(script=[], model=model or "mock")
    raise ProviderError(
        f"Unsupported or unset LLM_PROVIDER: {provider!r}. Set it to 'gemini' (or 'mock' for tests)."
    )


def get_llm_provider(
    settings: Settings | None = None, *, min_request_interval: float = 0.0
) -> LLMProvider:
    from repo_core.config import get_settings

    settings = settings or get_settings()
    return build_llm_provider(
        provider=settings.llm_provider,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        min_request_interval=min_request_interval,
    )


def build_embedding_provider(
    *,
    provider: str,
    api_key: str = "",
    model: str = "",
    dimensions: int = 1024,
) -> EmbeddingProvider:
    name = (provider or "").strip().lower()
    if not name or name in {"mock", "fake", "hash"}:
        from repo_providers.mock_embeddings import MockEmbeddingProvider

        return MockEmbeddingProvider(model=model or "mock-hash", dimensions=dimensions)
    if name == "voyage":
        from repo_providers.voyage import VoyageEmbeddingProvider

        return VoyageEmbeddingProvider(
            api_key=api_key,
            model=model or "voyage-code-3",
            dimensions=dimensions,
        )
    raise ProviderError(
        f"Unsupported EMBEDDING_PROVIDER: {provider!r}. Use 'voyage' or 'mock'."
    )


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    from repo_core.config import get_settings

    settings = settings or get_settings()
    return build_embedding_provider(
        provider=settings.embedding_provider,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dims,
    )
