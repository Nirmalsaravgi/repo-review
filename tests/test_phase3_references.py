"""Phase 3 C2 — reference (call site + import) extraction."""

from __future__ import annotations

from pathlib import Path

from repo_parsing import extract_references

FIXTURES = Path(__file__).parent / "fixtures" / "graph"
P2_FIXTURES = Path(__file__).parent / "fixtures" / "parsing"


def test_python_imports_and_calls() -> None:
    source = (FIXTURES / "service.py").read_bytes()
    refs = extract_references("service.py", source, language="python")

    # `from gateway import charge_card, refund`
    modules = {imp.module for imp in refs.imports}
    assert "gateway" in modules
    imported = {name for imp in refs.imports for name, _ in imp.names}
    assert {"charge_card", "refund"} <= imported

    call_names = {c.name for c in refs.calls}
    assert "charge_card" in call_names
    assert "refund" in call_names


def test_python_method_call_receiver() -> None:
    source = b"import json\n\ndef f(x):\n    return json.dumps(x)\n"
    refs = extract_references("m.py", source, language="python")
    dumps = [c for c in refs.calls if c.name == "dumps"]
    assert dumps and dumps[0].receiver == "json"
    assert any(imp.module == "json" for imp in refs.imports)


def test_python_relative_import_flag() -> None:
    source = b"from .gateway import charge_card\n"
    refs = extract_references("pkg/service.py", source, language="python")
    assert refs.imports and refs.imports[0].is_relative is True


def test_typescript_imports_and_calls() -> None:
    source = (
        b"import { charge } from './gateway';\n"
        b"import React from 'react';\n"
        b"export function run(n: number) {\n"
        b"  return charge(n);\n"
        b"}\n"
    )
    refs = extract_references("run.ts", source, language="typescript")
    modules = {imp.module for imp in refs.imports}
    assert "./gateway" in modules
    assert "react" in modules
    assert any(imp.is_relative for imp in refs.imports if imp.module == "./gateway")
    assert "charge" in {c.name for c in refs.calls}


def test_unsupported_language_is_empty() -> None:
    refs = extract_references("README.md", b"# hi\n")
    assert refs.calls == [] and refs.imports == []
