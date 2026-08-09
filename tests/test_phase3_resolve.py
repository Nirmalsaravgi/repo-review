"""Phase 3 C3 — import resolution + edge construction (pure resolver)."""

from __future__ import annotations

from pathlib import Path

from repo_parsing import extract_references, extract_symbols
from worker.ingest.graph import (
    SymbolSpan,
    resolve_graph,
    resolve_module_to_path,
)

FIXTURES = Path(__file__).parent / "fixtures" / "graph"


def _spans_for(path: str, source: bytes) -> list[SymbolSpan]:
    out: list[SymbolSpan] = []
    for i, s in enumerate(extract_symbols(path, source)):
        out.append(
            SymbolSpan(
                symbol_id=f"{path}:{i}:{s.name}",
                name=s.name,
                kind=s.kind,
                start_byte=s.start_byte,
                end_byte=s.end_byte,
                file_path=path,
            )
        )
    return out


def _build_from_fixtures() -> list:
    symbols_by_path: dict[str, list[SymbolSpan]] = {}
    refs_by_path = {}
    for name in ("service.py", "gateway.py"):
        source = (FIXTURES / name).read_bytes()
        symbols_by_path[name] = _spans_for(name, source)
        refs_by_path[name] = extract_references(name, source, language="python")
    return resolve_graph(symbols_by_path, refs_by_path)


def test_import_resolved_call_edge() -> None:
    edges = _build_from_fixtures()
    calls = [e for e in edges if e.kind == "calls"]
    # checkout() calls charge_card() → resolved via `from gateway import charge_card`
    resolved = [
        e
        for e in calls
        if e.dst_name == "charge_card" and e.resolution_method == "import_resolved"
    ]
    assert resolved, f"expected import-resolved charge_card edge, got {[e.dst_name for e in calls]}"
    e = resolved[0]
    assert e.confidence >= 0.9
    assert "service.py" in str(e.src_symbol_id)  # enclosed by checkout in service.py
    assert "gateway.py" in str(e.dst_symbol_id)  # target defined in gateway.py


def test_imports_edge_present() -> None:
    edges = _build_from_fixtures()
    imports = [e for e in edges if e.kind == "imports"]
    assert any(
        e.dst_name == "gateway.py" and e.resolution_method == "import_resolved" for e in imports
    )


def test_name_match_lower_confidence() -> None:
    # helper() defined in a.py, called in b.py with no import → name match, low confidence
    a = b"def helper(x):\n    return x\n"
    b = b"def run(x):\n    return helper(x)\n"
    symbols = {"a.py": _spans_for("a.py", a), "b.py": _spans_for("b.py", b)}
    refs = {
        "a.py": extract_references("a.py", a, language="python"),
        "b.py": extract_references("b.py", b, language="python"),
    }
    edges = resolve_graph(symbols, refs)
    helper_edges = [e for e in edges if e.kind == "calls" and e.dst_name == "helper"]
    assert helper_edges
    assert helper_edges[0].resolution_method == "name_match"
    assert helper_edges[0].confidence < 0.9


def test_resolve_module_to_path_python() -> None:
    known = {"gateway.py", "pkg/util.py", "pkg/__init__.py"}
    assert (
        resolve_module_to_path(
            "gateway", from_path="service.py", is_relative=False, known_paths=known
        )
        == "gateway.py"
    )
    assert (
        resolve_module_to_path("pkg.util", from_path="app.py", is_relative=False, known_paths=known)
        == "pkg/util.py"
    )
    assert (
        resolve_module_to_path(
            ".util", from_path="pkg/service.py", is_relative=True, known_paths=known
        )
        == "pkg/util.py"
    )


def test_resolve_module_to_path_js() -> None:
    known = {"src/gateway.ts", "src/lib/index.ts"}
    assert (
        resolve_module_to_path(
            "./gateway", from_path="src/run.ts", is_relative=True, known_paths=known
        )
        == "src/gateway.ts"
    )
    assert (
        resolve_module_to_path("./lib", from_path="src/run.ts", is_relative=True, known_paths=known)
        == "src/lib/index.ts"
    )
    # bare specifier → external, unresolved
    assert (
        resolve_module_to_path(
            "react", from_path="src/run.ts", is_relative=False, known_paths=known
        )
        is None
    )
