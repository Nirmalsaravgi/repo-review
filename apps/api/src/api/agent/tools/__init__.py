"""Agent tools (Slice 1): path-safe, bounded file operations over a repo clone.

Public surface used by later slices:
- `TOOL_SCHEMAS` — vendor-neutral JSON Schemas (Slice 2 maps to Gemini).
- `run_tool` / `arun_tool` — execute a model-requested tool call (Slice 3).
The individual functions are exported for direct use and testing.
"""

from api.agent.tools.base import ToolError, resolve_within
from api.agent.tools.filesystem import (
    GlobResult,
    GrepMatch,
    GrepResult,
    ListDirResult,
    ReadFileResult,
    glob_files,
    grep,
    list_dir,
    read_file,
)
from api.agent.tools.registry import TOOL_NAMES, TOOL_SCHEMAS, arun_tool, run_tool

__all__ = [
    "TOOL_NAMES",
    "TOOL_SCHEMAS",
    "GlobResult",
    "GrepMatch",
    "GrepResult",
    "ListDirResult",
    "ReadFileResult",
    "ToolError",
    "arun_tool",
    "glob_files",
    "grep",
    "list_dir",
    "read_file",
    "resolve_within",
    "run_tool",
]
