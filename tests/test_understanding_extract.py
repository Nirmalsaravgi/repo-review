"""Understanding extractors — Layer A facts, no Postgres."""

from __future__ import annotations

from pathlib import Path

from repo_parsing.understanding import (
    EndpointFact,
    JobFact,
    assign_domain,
    assign_layer,
    detect_externals,
    extract_endpoints_from_source,
    extract_jobs_from_source,
    heuristic_narrative,
    parse_manifest,
    parse_route_edge_name,
    pick_catalog_seed,
    sanitize_narrative,
    scan_tree,
)


def test_assign_layer_and_domain() -> None:
    assert assign_layer("apps/api/src/api/routes/chat.py") == "api"
    assert assign_domain("apps/api/src/api/routes/chat.py") == "API routes"
    assert assign_layer("apps/web/src/app/page.tsx") == "web"
    assert assign_layer("app/product/[handle]/page.tsx") == "web"
    assert assign_layer("app/api/revalidate/route.ts") == "api"
    assert assign_layer("components/cart/index.tsx") == "web"
    assert assign_domain("app/product/[handle]/page.tsx") == "Web"
    assert assign_domain("apps/web/src/app/page.tsx") == "Web"
    assert assign_layer("apps/worker/src/worker/ingest/parse.py") == "worker"
    assert assign_layer("alembic/versions/0001_initial.py") == "data"
    assert assign_layer("tests/test_foo.py") == "test"
    assert assign_domain("apps/api/src/api/bot/review.py") == "Review bot"
    assert assign_domain("packages/core/src/repo_core/models.py") == "Core"
    assert assign_layer("packages/core/src/repo_core/models.py") == "lib"
    assert assign_layer("alembic/versions/0001_initial.py") == "data"


def test_fastapi_and_express_endpoints() -> None:
    py = '''
from fastapi import APIRouter
router = APIRouter()

@router.get("/repos/{id}/brief")
async def get_brief():
    return {}

@router.post("/repos/select")
async def select():
    return {}
'''
    eps = extract_endpoints_from_source("apps/api/src/api/routes/understanding.py", py)
    paths = {(e.method, e.path) for e in eps}
    assert ("GET", "/repos/{id}/brief") in paths
    assert ("POST", "/repos/select") in paths

    js = 'app.get("/health", (req, res) => res.send("ok"))\n'
    js_eps = extract_endpoints_from_source("apps/api/server.js", js)
    assert any(e.method == "GET" and e.path == "/health" for e in js_eps)


def test_next_route_file_convention() -> None:
    src = "export async function GET() { return Response.json({}) }\n"
    eps = extract_endpoints_from_source("apps/web/src/app/api/hello/route.ts", src)
    assert eps
    assert eps[0].method == "GET"
    assert eps[0].path == "/api/hello"
    assert eps[0].source == "file_convention"


def test_parse_manifest_package_json() -> None:
    text = '{"dependencies":{"next":"15.1.0","react":"19.0.0"},"devDependencies":{"typescript":"5"}}'
    mf = parse_manifest("apps/web/package.json", text)
    assert mf is not None
    assert mf.kind == "node"
    assert "Next.js" in mf.frameworks
    assert "React" in mf.frameworks


def test_detect_externals_from_imports_and_env() -> None:
    ext = detect_externals(
        ["stripe", "asyncpg", "celery"],
        "GITHUB_APP_ID=\nVOYAGE_API_KEY=\n",
    )
    names = {e.name for e in ext}
    assert "Stripe" in names
    assert "Postgres" in names
    assert "Celery" in names
    assert "GitHub" in names
    assert "Voyage" in names


def test_parse_route_edge_name() -> None:
    assert parse_route_edge_name("GET /health") == ("GET", "/health")
    assert parse_route_edge_name("not a route") is None


def test_scan_tree_on_mini_repo(tmp_path: Path) -> None:
    (tmp_path / "apps" / "api").mkdir(parents=True)
    (tmp_path / "apps" / "api" / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health():\n    return {}\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["fastapi>=0.115", "asyncpg"]\n',
        encoding="utf-8",
    )
    facts = scan_tree(tmp_path)
    assert facts.file_count >= 1
    assert "python" in facts.languages
    assert any(e.path == "/health" for e in facts.endpoints)
    assert any(e.kind == "http_app" for e in facts.entry_points)
    assert "FastAPI" in facts.frameworks
    narrative = heuristic_narrative(facts)
    assert "Python" in narrative["summary"]
    assert narrative["suggested_questions"]


def test_celery_and_webhook_jobs() -> None:
    jobs = extract_jobs_from_source(
        "apps/worker/src/worker/ingest/code_pipeline.py",
        "@celery_app.task(name='worker.ingest.index_code')\ndef index_code():\n    return {}\n",
    )
    assert any(j.name == "index_code" and j.kind == "celery" for j in jobs)
    hooks = extract_jobs_from_source(
        "apps/api/src/api/routes/webhooks.py",
        "async def github_webhook():\n    return {}\n",
    )
    assert any(j.name == "github_webhook" and j.kind == "webhook" for j in hooks)


def test_sanitize_narrative_rejects_invented_folders() -> None:
    facts = scan_tree(Path("tests/fixtures/understanding"))
    dirty = {
        "summary": "A shop that charges cards.",
        "domains": [
            {"name": "Orders", "folders": ["apps/api"], "why": "HTTP handlers"},
            {"name": "Invented", "folders": ["apps/secret-ai"], "why": "not real"},
        ],
        "architecture_layers": ["web", "api", "quantum"],
    }
    clean = sanitize_narrative(dirty, facts)
    names = {d["name"] for d in clean["domains"]}
    assert "Invented" not in names
    folders = {f for d in clean["domains"] for f in d["folders"]}
    assert "apps/secret-ai" not in folders
    assert "quantum" not in clean["architecture_layers"]
    assert "A shop that charges cards." in clean["summary"]


def test_pick_catalog_seed_prefers_endpoint() -> None:
    endpoints = [
        EndpointFact("POST", "/orders", "apps/api/orders.py", handler_name="create_order"),
        EndpointFact("GET", "/login", "apps/api/main.py", handler_name="login"),
    ]
    jobs = [JobFact("process_order", "apps/worker/worker.py", "celery")]
    seed = pick_catalog_seed("How does POST /orders work?", endpoints=endpoints, jobs=jobs)
    assert seed is not None
    assert seed.kind == "http"
    assert seed.path == "/orders"
    job_seed = pick_catalog_seed("How does process_order work?", endpoints=endpoints, jobs=jobs)
    assert job_seed is not None
    assert job_seed.kind == "job"


def test_scan_tree_skips_nested_clones(tmp_path: Path) -> None:
    (tmp_path / "apps" / "api").mkdir(parents=True)
    (tmp_path / "apps" / "api" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    clone = tmp_path / "data" / "clones" / "org" / "repo" / "src" / "app"
    clone.mkdir(parents=True)
    (clone / "page.tsx").write_text("export default function Page() { return null }\n", encoding="utf-8")
    facts = scan_tree(tmp_path)
    assert all("data/clones" not in p for p in facts.folders)
    assert not any("data/clones" in e.path for e in facts.entry_points)


def test_component_members_filter_by_layer_domain() -> None:
    from api.graph.architecture import FileMember, SymbolMember
    from repo_parsing.understanding import component_key

    paths = [
        ("Home", "function", 1, 20, "apps/web/src/app/page.tsx"),
        ("select_repo", "function", 10, 40, "apps/api/src/api/routes/repos.py"),
        ("clone", "function", 5, 80, "packages/core/src/repo_core/clone.py"),
    ]
    files: dict[str, int] = {}
    for name, kind, start, end, path in paths:
        layer, domain = component_key(path)
        if layer != "web" or domain != "Web":
            continue
        files[path] = files.get(path, 0) + 1
        assert kind == "function"
        assert start < end
        _ = SymbolMember(name=name, kind=kind, path=path, start_line=start, end_line=end)
    assert list(files) == ["apps/web/src/app/page.tsx"]
    assert FileMember(path="apps/web/src/app/page.tsx", symbol_count=1, start_line=1).symbol_count == 1
    from api.graph.architecture import aggregate_architecture
    from api.graph.blast import GraphEdge, GraphNode

    nodes = {
        "w": GraphNode("w", "Home", "apps/web/src/app/page.tsx", "function"),
        "a": GraphNode("a", "select_repo", "apps/api/src/api/routes/repos.py", "function"),
        "c": GraphNode("c", "clone", "packages/core/src/repo_core/clone.py", "function"),
    }
    edges = [
        GraphEdge("w", "a", "calls", 0.9),
        GraphEdge("a", "c", "imports", 0.95),
    ]
    g = aggregate_architecture(edges, nodes)
    labels = {n.label for n in g.nodes}
    assert "Web" in labels
    assert "API routes" in labels or "API" in labels
    assert "Core" in labels
    assert g.edges  # cross-component links kept
