"""Understanding models register for RLS and construct."""

from __future__ import annotations

from uuid import uuid4

from repo_core.models import (
    TENANT_TABLES,
    AtomicSummary,
    Brief,
    Component,
    DocPlan,
    Endpoint,
    External,
    Feature,
    Flow,
    Module,
    RepoProfile,
    ValidationResult,
    WikiArtifact,
)


def test_understanding_tables_registered() -> None:
    for table in (
        "endpoints",
        "externals",
        "components",
        "flows",
        "briefs",
        "repo_profiles",
        "modules",
        "features",
        "doc_plans",
        "atomic_summaries",
        "wiki_artifacts",
        "validation_results",
    ):
        assert table in TENANT_TABLES


def test_understanding_models_construct() -> None:
    org, repo = uuid4(), uuid4()
    ep = Endpoint(
        id=uuid4(),
        org_id=org,
        repo_id=repo,
        method="GET",
        path="/health",
        source="decorator",
    )
    ext = External(id=uuid4(), org_id=org, repo_id=repo, name="Postgres", kind="database")
    comp = Component(id=uuid4(), org_id=org, repo_id=repo, name="API", layer="api")
    flow = Flow(id=uuid4(), org_id=org, repo_id=repo, title="GET /health", kind="http")
    brief = Brief(
        id=uuid4(),
        org_id=org,
        repo_id=repo,
        indexed_sha="abc",
        facts={"file_count": 1},
        narrative={"summary": "A demo"},
    )
    assert ep.path == "/health"
    assert ext.kind == "database"
    assert comp.layer == "api"
    assert flow.kind == "http"
    assert brief.facts["file_count"] == 1
    profile = RepoProfile(
        id=uuid4(),
        org_id=org,
        repo_id=repo,
        indexed_sha="abc",
        application_style="backend_api",
        analyzers=["http", "db"],
        confidence=0.8,
    )
    module = Module(
        id=uuid4(),
        org_id=org,
        repo_id=repo,
        name="Billing",
        kind="module",
        cohesion=0.8,
        coupling=0.2,
        summary=None,
    )
    feature = Feature(
        id=uuid4(),
        org_id=org,
        repo_id=repo,
        name="POST /invoices/{id}/approve",
        confidence=0.7,
        summary=None,
    )
    assert profile.application_style == "backend_api"
    assert module.summary is None
    assert feature.summary is None
    plan = DocPlan(id=uuid4(), org_id=org, repo_id=repo, categories=[{"key": "overview", "enabled": True}])
    atomic = AtomicSummary(
        id=uuid4(),
        org_id=org,
        repo_id=repo,
        target_kind="symbol",
        target_id=uuid4(),
        summary="charge is a function in billing.py.",
        content_sha="abc",
        facts={"responsibility": "charge"},
        inferences=[],
    )
    page = WikiArtifact(
        id=uuid4(),
        org_id=org,
        repo_id=repo,
        type="module",
        slug="module/billing",
        title="Billing",
        body="Billing covers billing.py.",
        status="staging",
    )
    check = ValidationResult(
        id=uuid4(),
        org_id=org,
        repo_id=repo,
        artifact_id=page.id,
        check_type="source_path",
        status="pass",
    )
    assert plan.categories[0]["key"] == "overview"
    assert atomic.facts["responsibility"] == "charge"
    assert page.status == "staging"
    assert check.status == "pass"


def test_index_understanding_task_registered() -> None:
    from worker import celery_app

    assert "worker.ingest.index_understanding" in celery_app.tasks
