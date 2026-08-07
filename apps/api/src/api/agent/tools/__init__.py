"""Agent tools: path-safe filesystem ops + Phase 1 git intelligence tools.

Public surface:
- `TOOL_SCHEMAS` — vendor-neutral JSON Schemas.
- `run_tool` / `arun_tool` — execute a model-requested tool call.
- `ToolContext` — root + optional org/repo/redis for git tools.
"""

from api.agent.tools.base import ToolError, resolve_within
from api.agent.tools.context import ToolContext
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
    "ToolContext",
    "ToolError",
    "arun_tool",
    "glob_files",
    "grep",
    "list_dir",
    "read_file",
    "resolve_within",
    "run_tool",
]
