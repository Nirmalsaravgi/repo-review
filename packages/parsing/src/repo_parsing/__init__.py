"""Code parsing: tree-sitter symbol extraction (Phase 2)."""

from repo_parsing.chunking import BuiltChunk, build_scope_header, chunk_file_symbols
from repo_parsing.extract import ExtractedSymbol, extract_file, extract_symbols
from repo_parsing.languages import DETECTED_EXTENSIONS, detect_language
from repo_parsing.references import (
    CallSite,
    ExtractedReferences,
    ImportRef,
    extract_references,
)
from repo_parsing.understanding import (
    CatalogSeed,
    EndpointFact,
    JobFact,
    UnderstandingFacts,
    assign_domain,
    assign_layer,
    extract_endpoints_from_source,
    extract_jobs_from_source,
    heuristic_narrative,
    pick_catalog_seed,
    sanitize_narrative,
    scan_tree,
)

__all__ = [
    "BuiltChunk",
    "CallSite",
    "CatalogSeed",
    "DETECTED_EXTENSIONS",
    "ExtractedReferences",
    "ExtractedSymbol",
    "ImportRef",
    "JobFact",
    "build_scope_header",
    "chunk_file_symbols",
    "detect_language",
    "extract_file",
    "extract_references",
    "extract_symbols",
    "EndpointFact",
    "UnderstandingFacts",
    "assign_domain",
    "assign_layer",
    "extract_endpoints_from_source",
    "extract_jobs_from_source",
    "heuristic_narrative",
    "pick_catalog_seed",
    "sanitize_narrative",
    "scan_tree",
]
