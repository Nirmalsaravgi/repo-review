"""Phase 2 P2 — token-budgeted signature repo map."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from api.agent.repo_map import (
    MapSymbol,
    build_file_tree_repo_map,
    build_live_signature_repo_map,
    build_repo_map,
    format_signature_repo_map,
)


def test_format_signatures_nests_methods() -> None:
    class_id, method_id = uuid4(), uuid4()
    symbols = [
        MapSymbol(
            path="src/app.py",
            name="Greeter",
            kind="class",
            signature="class Greeter",
            start_line=1,
            symbol_id=class_id,
        ),
        MapSymbol(
            path="src/app.py",
            name="hello",
            kind="method",
            signature="def hello(self) -> str",
            start_line=3,
            parent_symbol_id=class_id,
            symbol_id=method_id,
        ),
        MapSymbol(
            path="src/app.py",
            name="standalone",
            kind="function",
            signature="def standalone(x: int) -> int",
            start_line=10,
            symbol_id=uuid4(),
        ),
    ]
    text = format_signature_repo_map(symbols)
    assert "src/app.py" in text
    assert "class Greeter" in text
    assert "def hello(self) -> str" in text
    assert "def standalone" in text
    # method indented under class
    assert "    def hello" in text


def test_token_budget_truncates() -> None:
    symbols = [
        MapSymbol(
            path=f"pkg/mod_{i}.py",
            name=f"fn_{i}",
            kind="function",
            signature=f"def fn_{i}(aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa):",
            start_line=1,
            symbol_id=uuid4(),
        )
        for i in range(80)
    ]
    text = format_signature_repo_map(symbols, max_tokens=80)
    assert "truncated" in text
    assert text.startswith("Repository map")


def test_live_map_from_fixture_tree(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text(
        "class Greeter:\n    def hello(self):\n        return 1\n\ndef top():\n    pass\n",
        encoding="utf-8",
    )
    text = build_live_signature_repo_map(tmp_path)
    assert "src/app.py" in text
    assert "Greeter" in text or "class Greeter" in text
    assert "top" in text


def test_build_repo_map_prefers_symbols(tmp_path: Path) -> None:
    (tmp_path / "only.md").write_text("# hi\n", encoding="utf-8")
    symbols = [
        MapSymbol(
            path="virtual.py",
            name="from_db",
            kind="function",
            signature="def from_db():",
            start_line=1,
            symbol_id=uuid4(),
        )
    ]
    text = build_repo_map(tmp_path, symbols=symbols)
    assert "from_db" in text
    assert "virtual.py" in text


def test_file_tree_fallback_when_no_code(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("a\n", encoding="utf-8")
    text = build_repo_map(tmp_path)
    assert "Repository layout" in text or "README.md" in text
    # no python → live map empty → file tree
    assert "README.md" in text


def test_agent_uses_injected_repo_map(tmp_path: Path) -> None:
    from api.agent import Agent
    from repo_providers import Completion, MockProvider

    (tmp_path / "x.py").write_text("def a():\n    pass\n", encoding="utf-8")
    agent = Agent(
        provider=MockProvider([Completion(text="ok")]),
        root=tmp_path,
        repo_map_text="Repository map:\n  injected_marker",
    )
    messages = agent._initial_messages("hi", [])
    assert any("injected_marker" in (m.text or "") for m in messages)
