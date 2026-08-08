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

from api.agent.tools import git as git_tools
from api.agent.tools import search as search_tools
from api.agent.tools.base import ToolError
from api.agent.tools.context import ToolContext
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
            "Best for exact string / regex matches. For symbol names prefer "
            "find_symbol; for conceptual questions prefer search_code. Keep using "
            "grep when you need a precise pattern or path_filter."
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
    {
        "name": "search_code",
        "description": (
            "Hybrid search over the indexed repository (semantic chunks + lexical). "
            "Use for conceptual questions like 'how does checkout charge the card?'. "
            "Returns ranked file hits with snippets — then read_file to verify before citing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language or keyword query.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max hits (default 10, max 20).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "find_symbol",
        "description": (
            "Look up a code symbol by name (exact, prefix, or fuzzy) using the "
            "symbol index, with grep fallback. Prefer this over grep for "
            "identifiers like StripeGateway or handle_checkout."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Symbol name to find."},
                "limit": {
                    "type": "integer",
                    "description": "Max hits (default 10, max 20).",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "git_log",
        "description": (
            "Show recent commits that touched a path (or the whole repo). "
            "Use for history questions about when/how a file evolved."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-relative path (file or directory). Default '.'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max commits to return (default 20, max 40).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "git_blame",
        "description": (
            "Show which commit last touched a specific 1-based line in a file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-relative file path."},
                "line": {"type": "integer", "description": "1-based line number."},
            },
            "required": ["path", "line"],
        },
    },
    {
        "name": "who_owns",
        "description": (
            "Return ownership scores for a path from indexed git history "
            "(recency-weighted). Use for 'who knows this code?' questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-relative file or directory."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "why_here",
        "description": (
            "Explain why a line exists: blame → introducing commit → linked PR/issues "
            "when history is indexed. Summarize from the returned artifacts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "line": {"type": "integer"},
            },
            "required": ["path", "line"],
        },
    },
    {
        "name": "explain_diff",
        "description": (
            "Diff two git refs (commits, branches, or tags) and return per-file patches "
            "capped for context. Summarize architecturally, not line-by-line."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref_a": {"type": "string", "description": "Base ref (older)."},
                "ref_b": {"type": "string", "description": "Head ref (newer)."},
            },
            "required": ["ref_a", "ref_b"],
        },
    },
    {
        "name": "compare_releases",
        "description": "Diff two release tags (same as explain_diff for tags).",
        "parameters": {
            "type": "object",
            "properties": {
                "tag_a": {"type": "string"},
                "tag_b": {"type": "string"},
            },
            "required": ["tag_a", "tag_b"],
        },
    },
]

TOOL_NAMES = frozenset(s["name"] for s in TOOL_SCHEMAS)

_ASYNC_TOOLS = frozenset({"who_owns", "why_here", "search_code", "find_symbol"})


def _as_dict(result: Any) -> Any:
    return asdict(result) if is_dataclass(result) else result


def _ctx(root_or_ctx: Path | ToolContext) -> ToolContext:
    if isinstance(root_or_ctx, ToolContext):
        return root_or_ctx
    return ToolContext.from_root(root_or_ctx)


def run_tool(
    name: str, arguments: dict[str, Any] | None, root_or_ctx: Path | ToolContext
) -> dict[str, Any]:
    """Execute a sync tool by name. Async tools must use `arun_tool`."""
    args = arguments or {}
    ctx = _ctx(root_or_ctx)
    root = ctx.root
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
        elif name == "git_log":
            result = git_tools.git_log(ctx, args.get("path", "."), args.get("limit", 20))
        elif name == "git_blame":
            if "path" not in args or "line" not in args:
                raise ToolError("Missing required arguments: path, line")
            result = git_tools.git_blame(ctx, args["path"], int(args["line"]))
        elif name == "explain_diff":
            if "ref_a" not in args or "ref_b" not in args:
                raise ToolError("Missing required arguments: ref_a, ref_b")
            result = git_tools.explain_diff(ctx, args["ref_a"], args["ref_b"])
        elif name == "compare_releases":
            if "tag_a" not in args or "tag_b" not in args:
                raise ToolError("Missing required arguments: tag_a, tag_b")
            result = git_tools.compare_releases(ctx, args["tag_a"], args["tag_b"])
        elif name in _ASYNC_TOOLS:
            return {
                "ok": False,
                "error": f"Tool {name} is async — use arun_tool",
            }
        else:
            return {
                "ok": False,
                "error": f"Unknown tool: {name}. Valid tools: {', '.join(sorted(TOOL_NAMES))}.",
            }
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "result": _as_dict(result)}


async def arun_tool(
    name: str, arguments: dict[str, Any] | None, root_or_ctx: Path | ToolContext
) -> dict[str, Any]:
    """Async dispatcher — filesystem/git sync tools run off-loop; DB tools await."""
    args = arguments or {}
    ctx = _ctx(root_or_ctx)
    try:
        if name == "who_owns":
            if "path" not in args:
                raise ToolError("Missing required argument: path")
            result = await git_tools.who_owns(ctx, args["path"])
            return {"ok": True, "result": result}
        if name == "why_here":
            if "path" not in args or "line" not in args:
                raise ToolError("Missing required arguments: path, line")
            result = await git_tools.why_here(ctx, args["path"], int(args["line"]))
            return {"ok": True, "result": result}
        if name == "search_code":
            if "query" not in args:
                raise ToolError("Missing required argument: query")
            result = await search_tools.search_code(ctx, args["query"], args.get("limit", 10))
            return {"ok": True, "result": result}
        if name == "find_symbol":
            if "name" not in args:
                raise ToolError("Missing required argument: name")
            result = await search_tools.find_symbol(ctx, args["name"], args.get("limit", 10))
            return {"ok": True, "result": result}
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    return await asyncio.to_thread(run_tool, name, arguments, ctx)
