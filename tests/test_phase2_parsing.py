"""Phase 2 P1 — symbol models, parser fixtures, index_code task registration."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from repo_core.models import TENANT_TABLES, Symbol
from repo_parsing import detect_language, extract_symbols
from repo_parsing.languages import SKIP_DIR_NAMES

FIXTURES = Path(__file__).parent / "fixtures" / "parsing"


def test_detect_language() -> None:
    assert detect_language("src/app.py") == "python"
    assert detect_language("src/App.tsx") == "tsx"
    assert detect_language("lib/util.ts") == "typescript"
    assert detect_language("x.md") is None


def test_extract_python_class_and_methods() -> None:
    source = (FIXTURES / "sample.py").read_bytes()
    syms = extract_symbols("sample.py", source, language="python")
    by_name = {s.name: s for s in syms}
    assert "Greeter" in by_name
    assert by_name["Greeter"].kind == "class"
    assert "hello" in by_name
    assert by_name["hello"].kind == "method"
    assert by_name["hello"].parent_index is not None
    assert syms[by_name["hello"].parent_index].name == "Greeter"
    assert "standalone" in by_name
    assert by_name["standalone"].kind == "function"
    assert any(s.kind == "import" for s in syms)


def test_extract_typescript_exports() -> None:
    source = (FIXTURES / "sample.ts").read_bytes()
    syms = extract_symbols("sample.ts", source, language="typescript")
    names = {s.name for s in syms}
    assert "PaymentGateway" in names or "charge" in names
    kinds = {s.kind for s in syms}
    assert "function" in kinds or "interface" in kinds or "class" in kinds


def test_skip_dirs_include_node_modules() -> None:
    assert "node_modules" in SKIP_DIR_NAMES
    assert ".venv" in SKIP_DIR_NAMES


def test_symbol_model_construct() -> None:
    org, repo, file_id = uuid4(), uuid4(), uuid4()
    sym = Symbol(
        id=uuid4(),
        org_id=org,
        repo_id=repo,
        file_id=file_id,
        name="hello",
        qualified_name="Greeter.hello",
        kind="method",
        signature="def hello(self) -> str",
        start_line=10,
        end_line=12,
        start_byte=100,
        end_byte=140,
    )
    assert sym.name == "hello"
    assert "symbols" in TENANT_TABLES


def test_index_code_task_registered() -> None:
    from worker import celery_app

    assert "worker.ingest.index_code" in celery_app.tasks


def test_iter_source_files_skips_venv(tmp_path: Path) -> None:
    from worker.ingest.parse import iter_source_files

    (tmp_path / "ok.py").write_text("def a():\n    pass\n", encoding="utf-8")
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "site.py").write_text("x = 1\n", encoding="utf-8")
    found = {rel for rel, _ in iter_source_files(tmp_path)}
    assert "ok.py" in found
    assert not any(".venv" in r for r in found)
