"""Tool schemas + dispatch.

`TOOL_SCHEMAS` is vendor-neutral (plain JSON Schema). The Gemini provider in
Slice 2 maps these into `FunctionDeclaration`s; the agent loop in Slice 3 calls
`arun_tool` to execute a model-requested call. Keeping the schema list here means
there is exactly one place that defines what the agent can do.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from api.agent.tools.base import ToolError
from api.agent.tools.filesystem import glob_files, grep, list_dir, read_file

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_dir",
        "description": (
            "List the immediate contents of a directory in the repository. "
            "Use this to orient yourself before reading files. The '.git' "
            "directory is hidden."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-root-relative directory path. Use '.' for the root.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a text file, optionally limited to a 1-based line range. Always "
            "prefer a range for large files. Line numbers in the result are real "
            "and can be cited."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-root-relative file path."},
                "start_line": {"type": "integer", "description": "First line to read (1-based)."},
                "end_line": {"type": "integer", "description": "Last line to read (inclusive)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "glob",
        "description": (
            "Find files by glob pattern relative to the repo root, e.g. '**/*.py' "
            "or 'src/**/*.ts'. Returns matching file paths."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, repo-relative."}
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": (
            "Search file contents for a regular expression across the repository. "
            "Best for exact identifiers, strings, and symbols. Returns matching "
            "lines with their file path and line number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression to search for."},
                "path_filter": {
                    "type": "string",
                    "description": "Optional glob to restrict which files are searched, e.g. '*.ts'.",
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default false).",
                },
            },
            "required": ["pattern"],
        },
    },
]

TOOL_NAMES = frozenset(s["name"] for s in TOOL_SCHEMAS)


def _as_dict(result: Any) -> Any:
    return asdict(result) if is_dataclass(result) else result


def run_tool(name: str, arguments: dict[str, Any] | None, root: Path) -> dict[str, Any]:
    """Execute a tool by name and return a structured envelope.

    Always returns `{"ok": bool, ...}` rather than raising, so a bad model-issued
    call becomes a tool error the agent can recover from — never a crashed loop.
    """
    args = arguments or {}
    try:
        if name == "list_dir":
            result = list_dir(root, args.get("path", "."))
        elif name == "read_file":
            if "path" not in args:
                raise ToolError("Missing required argument: path")
            result = read_file(root, args["path"], args.get("start_line"), args.get("end_line"))
        elif name == "glob":
            if "pattern" not in args:
                raise ToolError("Missing required argument: pattern")
            result = glob_files(root, args["pattern"])
        elif name == "grep":
            if "pattern" not in args:
                raise ToolError("Missing required argument: pattern")
            result = grep(
                root,
                args["pattern"],
                path_filter=args.get("path_filter"),
                ignore_case=bool(args.get("ignore_case", False)),
            )
        else:
            return {
                "ok": False,
                "error": f"Unknown tool: {name}. Valid tools: {', '.join(sorted(TOOL_NAMES))}.",
            }
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "result": _as_dict(result)}


async def arun_tool(name: str, arguments: dict[str, Any] | None, root: Path) -> dict[str, Any]:
    """Async wrapper — tools are blocking (filesystem/subprocess), so run off-loop.

    Lets the Slice 3 loop dispatch several tool calls concurrently with
    `asyncio.gather` without blocking the event loop.
    """
    return await asyncio.to_thread(run_tool, name, arguments, root)
