"""Repository profile: style and analyzer dispatch across repo kinds."""

from __future__ import annotations

from pathlib import Path

from repo_parsing.profile import ANALYZER_CATALOG, classify_profile, select_analyzers
from repo_parsing.understanding import scan_tree

FIXTURE = Path("tests/fixtures/understanding")


def _profile(root: Path, *, analyzers: list[str] | None = None):
    facts = scan_tree(root, analyzers=[] if analyzers is None else analyzers)
    profile = classify_profile(facts)
    select_analyzers(profile)
    return profile


def test_fullstack_fixture_profile() -> None:
    profile = _profile(FIXTURE)
    assert profile.application_style == "fullstack_monorepo"
    assert profile.confidence >= 0.45
    assert "http" in profile.analyzers
    assert "ui" in profile.analyzers
    assert "worker" in profile.analyzers
    decision = next(sig for sig in profile.signals if sig["detector"] == "decision")
    assert decision["detail"] == "backend_and_frontend"
    assert any(sig["weight"] > 0 and sig["detector"] != "decision" for sig in profile.signals)
    names = {hit.name: hit.confidence for hit in profile.frameworks}
    assert names["FastAPI"] >= 0.9
    assert names["Next.js"] >= 0.9


def test_backend_frontend_cli_and_library(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    (backend / "apps" / "api").mkdir(parents=True)
    (backend / "pyproject.toml").write_text('[project]\ndependencies = ["fastapi"]\n', encoding="utf-8")
    (backend / "apps" / "api" / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n",
        encoding="utf-8",
    )
    backend_profile = _profile(backend)
    assert backend_profile.application_style == "backend_api"
    assert "http" in backend_profile.analyzers
    assert "ui" not in backend_profile.analyzers

    frontend = tmp_path / "frontend"
    (frontend / "app").mkdir(parents=True)
    (frontend / "package.json").write_text(
        '{"dependencies":{"next":"15.0.0","react":"19.0.0"}}',
        encoding="utf-8",
    )
    (frontend / "app" / "page.tsx").write_text("export default function Page(){return null}\n", encoding="utf-8")
    frontend_profile = _profile(frontend)
    assert frontend_profile.application_style == "frontend"
    assert "ui" in frontend_profile.analyzers
    assert "worker" not in frontend_profile.analyzers

    cli = tmp_path / "cli"
    cli.mkdir()
    (cli / "pyproject.toml").write_text(
        '[project]\ndependencies = ["click"]\n\n[project.scripts]\nshop = "shop.cli:main"\n',
        encoding="utf-8",
    )
    (cli / "cli.py").write_text("import click\n\n@click.command()\ndef main():\n    pass\n", encoding="utf-8")
    cli_profile = _profile(cli)
    assert cli_profile.application_style == "cli"
    assert "cli" in cli_profile.analyzers
    assert "http" not in cli_profile.analyzers

    library = tmp_path / "library"
    (library / "src" / "payments").mkdir(parents=True)
    (library / "pyproject.toml").write_text('[project]\nname = "payments"\n', encoding="utf-8")
    (library / "src" / "payments" / "__init__.py").write_text("def charge():\n    return 1\n", encoding="utf-8")
    library_profile = _profile(library)
    assert library_profile.application_style == "library"
    assert "http" not in library_profile.analyzers
    assert "ui" not in library_profile.analyzers


def test_event_ml_and_unknown(tmp_path: Path) -> None:
    worker = tmp_path / "worker"
    worker.mkdir()
    (worker / "pyproject.toml").write_text('[project]\ndependencies = ["celery"]\n', encoding="utf-8")
    (worker / "worker.py").write_text("from celery import Celery\ncelery_app = Celery('q')\n", encoding="utf-8")
    event_profile = _profile(worker)
    assert event_profile.application_style == "event_driven"
    assert "event" in event_profile.analyzers
    assert "worker" in event_profile.analyzers
    assert "http" not in event_profile.analyzers

    ml = tmp_path / "ml"
    (ml / "prompts").mkdir(parents=True)
    (ml / "pyproject.toml").write_text('[project]\ndependencies = ["langchain"]\n', encoding="utf-8")
    (ml / "prompts" / "agent.py").write_text(
        "from langchain.tools import tool\n\n@tool\ndef search(q: str) -> str:\n    return q\n",
        encoding="utf-8",
    )
    ml_profile = _profile(ml)
    assert ml_profile.application_style == "ml_system"
    assert "ai" in ml_profile.analyzers

    empty = tmp_path / "empty"
    empty.mkdir()
    unknown = _profile(empty)
    assert unknown.application_style == "unknown"
    assert unknown.analyzers == list(ANALYZER_CATALOG)


def test_route_extraction_is_gated(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health():\n    return {}\n",
        encoding="utf-8",
    )
    gated = scan_tree(tmp_path, analyzers=["cli"])
    assert gated.endpoints == []
    assert gated.file_count >= 1
    full = scan_tree(tmp_path)
    assert any(ep.path == "/health" for ep in full.endpoints)
