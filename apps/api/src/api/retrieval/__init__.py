"""Hybrid retrieval: semantic (pgvector) + lexical (symbols/grep) + RRF.

Used by Phase 2 P5 agent tools; P4 ships the library and golden-set tests.
"""

from api.retrieval.hybrid import HybridRetriever
from api.retrieval.lexical import LexicalRetriever
from api.retrieval.rrf import reciprocal_rank_fusion
from api.retrieval.semantic import SemanticRetriever
from api.retrieval.types import RetrievalHit

__all__ = [
    "HybridRetriever",
    "LexicalRetriever",
    "RetrievalHit",
    "SemanticRetriever",
    "reciprocal_rank_fusion",
]
