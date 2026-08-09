"""Phase 3 C5 — dynamic edges: event-bus joins + route detection."""

from __future__ import annotations

from repo_parsing import extract_references, extract_symbols
from worker.ingest.graph import SymbolSpan, build_dynamic_edges, resolve_graph


def _spans_for(path: str, source: bytes) -> list[SymbolSpan]:
    return [
        SymbolSpan(
            symbol_id=f"{path}:{i}:{s.name}",
            name=s.name,
            kind=s.kind,
            start_byte=s.start_byte,
            end_byte=s.end_byte,
            file_path=path,
        )
        for i, s in enumerate(extract_symbols(path, source))
    ]


def _graph(sources: dict[str, bytes]):
    symbols = {p: _spans_for(p, src) for p, src in sources.items()}
    refs = {p: extract_references(p, src) for p, src in sources.items()}
    return symbols, refs


def test_emit_subscribe_join_across_files() -> None:
    # The plan's flagship §4.1 case: emit('user.logged_in') → its subscriber.
    producer = b"def do_login(bus):\n    bus.emit('user.logged_in')\n"
    consumer = b"def wire(bus):\n    bus.on('user.logged_in')\n"
    symbols, refs = _graph({"auth.py": producer, "audit.py": consumer})
    edges = build_dynamic_edges(symbols, refs)
    emits = [e for e in edges if e.kind == "emits"]
    assert emits, "expected an emits edge joining producer to consumer"
    e = emits[0]
    assert "auth.py" in str(e.src_symbol_id)  # enclosed by do_login
    assert "audit.py" in str(e.dst_symbol_id)  # enclosed by wire
    assert e.resolution_method == "string_literal"
    assert e.confidence < 0.5  # indicative only
    assert "user.logged_in" in (e.dst_name or "")


def test_no_join_when_event_names_differ() -> None:
    producer = b"def a(bus):\n    bus.emit('x')\n"
    consumer = b"def b(bus):\n    bus.on('y')\n"
    symbols, refs = _graph({"a.py": producer, "b.py": consumer})
    edges = build_dynamic_edges(symbols, refs)
    assert not [e for e in edges if e.kind == "emits"]


def test_route_detection() -> None:
    src = b"def register(app):\n    app.get('/health')\n"
    symbols, refs = _graph({"routes.py": src})
    edges = build_dynamic_edges(symbols, refs)
    routes = [e for e in edges if e.kind == "route"]
    assert routes
    assert routes[0].dst_name == "GET /health"
    assert routes[0].resolution_method == "route_convention"


def test_dynamic_edges_flow_through_resolve_graph() -> None:
    # resolve_graph should include dynamic edges alongside calls/imports.
    producer = b"def a(bus):\n    bus.emit('evt')\n"
    consumer = b"def b(bus):\n    bus.on('evt')\n"
    symbols, refs = _graph({"a.py": producer, "b.py": consumer})
    edges = resolve_graph(symbols, refs)
    assert any(e.kind == "emits" for e in edges)
