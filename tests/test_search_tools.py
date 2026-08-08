"""Phase 2 P5 — search_code / find_symbol agent tools."""

from __future__ import annotations

from pathlib import Path

import pytest
from api.agent.tools.context import ToolContext
from api.agent.tools.registry import TOOL_NAMES, TOOL_SCHEMAS, arun_tool
from api.agent.tools.search import find_symbol, search_code


def test_search_tools_registered() -> None:
    names = {s["name"] for s in TOOL_SCHEMAS}
    assert "search_code" in names
    assert "find_symbol" in names
    assert "grep" in names  # must keep Phase 0 tools
    assert "search_code" in TOOL_NAMES


@pytest.mark.asyncio
async def test_find_symbol_fallback_grep(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def handle_checkout(cart):\n    return sum(cart)\n",
        encoding="utf-8",
    )
    ctx = ToolContext.from_root(tmp_path)
    result = await find_symbol(ctx, "handle_checkout")
    assert result["mode"] == "lexical_fallback"
    assert any(h["path"].endswith("app.py") for h in result["hits"])


@pytest.mark.asyncio
async def test_search_code_fallback_grep(tmp_path: Path) -> None:
    (tmp_path / "pay.py").write_text(
        "def charge():\n    '''stripe payment'''\n    return 1\n",
        encoding="utf-8",
    )
    ctx = ToolContext.from_root(tmp_path)
    result = await search_code(ctx, "stripe payment")
    assert result["mode"] == "lexical_fallback"
    assert result["hits"] or result["note"]


@pytest.mark.asyncio
async def test_arun_tool_search_code(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    envelope = await arun_tool("search_code", {"query": "alpha"}, tmp_path)
    assert envelope["ok"] is True
    assert "hits" in envelope["result"]


@pytest.mark.asyncio
async def test_arun_tool_find_symbol_missing_arg(tmp_path: Path) -> None:
    envelope = await arun_tool("find_symbol", {}, tmp_path)
    assert envelope["ok"] is False
    assert "name" in envelope["error"].lower()
