"""Code parsing: tree-sitter symbol extraction (Phase 2)."""

from repo_parsing.chunking import BuiltChunk, build_scope_header, chunk_file_symbols
from repo_parsing.extract import ExtractedSymbol, extract_file, extract_symbols
from repo_parsing.languages import DETECTED_EXTENSIONS, detect_language

__all__ = [
    "BuiltChunk",
    "DETECTED_EXTENSIONS",
    "ExtractedSymbol",
    "build_scope_header",
    "chunk_file_symbols",
    "detect_language",
    "extract_file",
    "extract_symbols",
]
