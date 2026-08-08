"""Map file paths to tree-sitter language ids."""

from __future__ import annotations

from pathlib import PurePosixPath

# Extensions we parse in Phase 2 P1. Others are skipped silently.
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
}

DETECTED_EXTENSIONS = frozenset(_EXT_TO_LANG)

# Directories never walked for indexing.
SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".next",
        "dist",
        "build",
        "coverage",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "vendor",
        "third_party",
    }
)


def detect_language(path: str) -> str | None:
    """Return a tree-sitter language id, or None if unsupported."""
    ext = PurePosixPath(path.replace("\\", "/")).suffix.lower()
    return _EXT_TO_LANG.get(ext)
