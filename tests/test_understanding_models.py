"""Understanding models register for RLS and construct."""

from __future__ import annotations

from uuid import uuid4

from repo_core.models import TENANT_TABLES, Brief, Component, Endpoint, External, Flow


def test_understanding_tables_registered() -> None:
    for table in ("endpoints", "externals", "components", "flows", "briefs"):
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


def test_index_understanding_task_registered() -> None:
    from worker import celery_app

    assert "worker.ingest.index_understanding" in celery_app.tasks
