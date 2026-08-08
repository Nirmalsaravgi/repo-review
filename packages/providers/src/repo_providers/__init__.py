"""LLM provider abstraction (Slice 2). Gemini is the first implementation.

The agent loop depends only on `LLMProvider` and the message/event types here —
never on a vendor SDK. Embedding providers land in Phase 2 alongside them.
"""

from repo_providers.base import (
    Completion,
    LLMProvider,
    Message,
    ProviderError,
    Role,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolResult,
    Usage,
)
from repo_providers.embeddings import EmbeddingProvider
from repo_providers.factory import (
    build_embedding_provider,
    build_llm_provider,
    get_embedding_provider,
    get_llm_provider,
)
from repo_providers.mock import MockProvider
from repo_providers.mock_embeddings import MockEmbeddingProvider

__all__ = [
    "Completion",
    "EmbeddingProvider",
    "LLMProvider",
    "Message",
    "MockEmbeddingProvider",
    "MockProvider",
    "ProviderError",
    "Role",
    "StreamEvent",
    "TextDelta",
    "ToolCall",
    "ToolCallDelta",
    "ToolResult",
    "Usage",
    "build_embedding_provider",
    "build_llm_provider",
    "get_embedding_provider",
    "get_llm_provider",
]
