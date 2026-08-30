"""Deterministic repository-understanding extractors (no DB, no LLM).

Layer A facts: manifests, languages, frameworks, externals, entry points,
HTTP endpoints, and layer/domain assignment for a path.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from repo_parsing.languages import DETECTED_EXTENSIONS, SKIP_DIR_NAMES, detect_language

# --------------------------------------------------------------------------- #
# Catalogs
# --------------------------------------------------------------------------- #

_FRAMEWORK_IMPORTS: dict[str, str] = {
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "starlette": "Starlette",
    "celery": "Celery",
    "next": "Next.js",
    "react": "React",
    "express": "Express",
    "hono": "Hono",
    "nestjs": "NestJS",
    "vue": "Vue",
    "svelte": "Svelte",
    "sqlalchemy": "SQLAlchemy",
    "prisma": "Prisma",
    "alembic": "Alembic",
}

# import-or-package prefix → (display name, kind)
_SDK_CATALOG: dict[str, tuple[str, str]] = {
    "stripe": ("Stripe", "payments"),
    "boto3": ("AWS", "cloud"),
    "botocore": ("AWS", "cloud"),
    "@aws-sdk": ("AWS", "cloud"),
    "redis": ("Redis", "cache"),
    "ioredis": ("Redis", "cache"),
    "celery": ("Celery", "queue"),
    "sqlalchemy": ("SQLAlchemy / Postgres", "database"),
    "asyncpg": ("Postgres", "database"),
    "psycopg": ("Postgres", "database"),
    "psycopg2": ("Postgres", "database"),
    "prisma": ("Prisma", "database"),
    "openai": ("OpenAI", "llm"),
    "google.genai": ("Gemini", "llm"),
    "google-genai": ("Gemini", "llm"),
    "@google/genai": ("Gemini", "llm"),
    "voyageai": ("Voyage", "llm"),
    "anthropic": ("Anthropic", "llm"),
    "pygithub": ("GitHub", "vcs"),
    "github": ("GitHub", "vcs"),
    "@octokit": ("GitHub", "vcs"),
    "httpx": ("HTTP client", "http"),
    "axios": ("HTTP client", "http"),
    "requests": ("HTTP client", "http"),
    "sentry_sdk": ("Sentry", "observability"),
    "@sentry": ("Sentry", "observability"),
    "boto": ("AWS", "cloud"),
}

_ENV_HINTS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"DATABASE_URL|POSTGRES|PGHOST", re.I), "Postgres", "database"),
    (re.compile(r"REDIS", re.I), "Redis", "cache"),
    (re.compile(r"STRIPE", re.I), "Stripe", "payments"),
    (re.compile(r"GITHUB_APP|GITHUB_TOKEN|GH_TOKEN", re.I), "GitHub", "vcs"),
    (re.compile(r"OPENAI", re.I), "OpenAI", "llm"),
    (re.compile(r"GEMINI|GOOGLE_API|GOOGLE_GENAI", re.I), "Gemini", "llm"),
    (re.compile(r"VOYAGE", re.I), "Voyage", "llm"),
    (re.compile(r"ANTHROPIC|CLAUDE", re.I), "Anthropic", "llm"),
    (re.compile(r"S3_|AWS_", re.I), "AWS", "cloud"),
    (re.compile(r"SENTRY", re.I), "Sentry", "observability"),
]

_DECORATOR_ROUTE = re.compile(
    r"@(?:app|router|api|blueprint|bp)\.(?P<method>get|post|put|delete|patch|options|head|websocket)"
    r"\(\s*[\"'](?P<path>/[^\"']*)[\"']",
    re.I,
)
_FLASK_ROUTE = re.compile(
    r"@(?:app|bp|blueprint)\.route\(\s*[\"'](?P<path>/[^\"']*)[\"'](?P<rest>[^)]*)\)",
    re.I,
)
_EXPRESS_ROUTE = re.compile(
    r"(?:app|router)\.(?P<method>get|post|put|delete|patch|options|head)\(\s*[\"'](?P<path>/[^\"']*)[\"']",
    re.I,
)
_NEXT_HANDLER = re.compile(
    r"export\s+(?:async\s+)?function\s+(?P<method>GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\b"
)
_AUTH_HINT = re.compile(
    r"require_session|require_auth|Depends\(|authenticate|authorization|middleware",
    re.I,
)
_FASTAPI_APP = re.compile(r"\b(FastAPI|Flask|Celery)\s*\(")
_CREATE_APP = re.compile(r"\b(create_app|make_app)\b")
_CELERY_APP = re.compile(r"\bcelery_app\b|\bCelery\s*\(")
_CELERY_TASK_DEF = re.compile(
    r"@(?:[\w.]+\.)?(?:shared_task|task)\s*(?:\([^)]*\))?\s*\n(?:async\s+)?def\s+(?P<name>\w+)",
    re.M,
)
_STOP_TOKENS = frozenset(
    {
        "the",
        "how",
        "does",
        "do",
        "what",
        "when",
        "is",
        "are",
        "a",
        "an",
        "this",
        "that",
        "work",
        "works",
        "happen",
        "happens",
        "called",
        "call",
        "and",
        "for",
        "with",
        "from",
        "end",
        "to",
        "of",
        "in",
        "on",
        "it",
        "its",
    }
)

_SKIP_FILE_PREFIXES = ("test_",)
_MANIFEST_NAMES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "go.mod",
        "Cargo.toml",
        "Gemfile",
        "composer.json",
        "Pipfile",
    }
)
_ENV_NAMES = frozenset({".env.example", ".env.sample", ".env.template"})

LAYER_ORDER = ("web", "api", "lib", "worker", "data", "external", "test")


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ManifestFact:
    path: str
    kind: str
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExternalFact:
    name: str
    kind: str
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.6


@dataclass(slots=True)
class EntryPointFact:
    path: str
    kind: str  # http_app | worker | page | cli | webhook
    name: str
    evidence: str = ""


@dataclass(slots=True)
class EndpointFact:
    method: str
    path: str
    file_path: str
    handler_name: str | None = None
    source: str = "decorator"
    auth_hint: str = "unknown"


@dataclass(slots=True)
class JobFact:
    name: str
    path: str
    kind: str = "celery"  # celery | webhook


@dataclass(slots=True)
class CatalogSeed:
    """Best HTTP/job/webhook seed for a 'how does X work' question. Pure ranking."""

    kind: str  # http | job | webhook
    title: str
    score: float
    handler_name: str | None = None
    file_path: str | None = None
    method: str | None = None
    path: str | None = None


@dataclass(slots=True)
class UnderstandingFacts:
    languages: dict[str, int] = field(default_factory=dict)
    frameworks: list[str] = field(default_factory=list)
    file_count: int = 0
    loc: int = 0
    manifests: list[ManifestFact] = field(default_factory=list)
    externals: list[ExternalFact] = field(default_factory=list)
    entry_points: list[EntryPointFact] = field(default_factory=list)
    endpoints: list[EndpointFact] = field(default_factory=list)
    jobs: list[JobFact] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Path → layer / domain
# --------------------------------------------------------------------------- #


def normalize_path(path: str) -> str:
    return (path or "").replace("\\", "/").lstrip("./")


def assign_layer(path: str) -> str:
    """Heuristic architecture layer. Deterministic; no LLM."""
    p = normalize_path(path).lower()
    name = p.rsplit("/", 1)[-1]
    if (
        "/tests/" in f"/{p}/"
        or "/test/" in f"/{p}/"
        or "/__tests__/" in f"/{p}/"
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
    ):
        return "test"
    if any(x in p for x in ("alembic/", "/migrations/", "migrations/")):
        return "data"
    if ("/models.py" in f"/{p}" or "/models/" in f"/{p}/") and not p.startswith(
        ("packages/", "libs/")
    ):
        return "data"
    # Next.js App Router (repo-root `app/` or `src/app/`)
    if p.startswith("app/") or "/app/" in f"/{p}":
        if "/api/" in f"/{p}/" or p.endswith("/route.ts") or p.endswith("/route.js"):
            return "api"
        return "web"
    if p.startswith("components/") or p.startswith("pages/") or p.startswith("src/components"):
        return "web"
    if any(
        x in p
        for x in (
            "apps/web",
            "frontend/",
            "src/pages",
        )
    ) and not p.startswith("apps/api"):
        return "web"
    if any(x in p for x in ("apps/worker", "/workers/", "/worker/", "celery", "/jobs/", "/tasks/")):
        if "node_modules" not in p:
            return "worker"
    if any(x in p for x in ("apps/api", "backend/", "/routes/", "/controllers/", "webhooks")):
        return "api"
    if p.startswith("packages/") or p.startswith("libs/"):
        return "lib"
    return "lib"


def assign_domain(path: str) -> str:
    """Human-readable domain from a file path. Folders only — never invented."""
    p = normalize_path(path)
    parts = [x for x in p.split("/") if x]
    low = p.lower()

    if assign_layer(p) == "test":
        return "Tests"
    if "alembic" in low or "/migrations/" in f"/{low}/":
        return "Migrations"
    if p.startswith("app/") or "/app/" in f"/{p}":
        if "/api/" in f"/{p}/":
            return "API"
        return "Web"
    if p.startswith("components/") or p.startswith("pages/"):
        return "Web"
    if "apps/worker" in low or "/worker/" in f"/{low}/":
        if "/bot/" in f"/{low}/" or "/ingest/" in f"/{low}/":
            return _title(parts[-2] if len(parts) >= 2 else "Worker")
        return "Worker"
    if "/bot/" in f"/{low}/" or "/pr_review" in low:
        return "Review bot"
    if "/agent/" in f"/{low}/":
        return "Agent"
    if "/routes/" in f"/{low}/":
        return "API routes"
    if "apps/api" in low or low.startswith("backend/"):
        return "API"
    if parts and parts[0] == "packages" and len(parts) >= 2:
        return _title(parts[1])
    if parts and parts[0] in {"apps", "src"} and len(parts) >= 2:
        return _title(parts[1])
    return _title(parts[0] if parts else "Root")


def component_key(path: str) -> tuple[str, str]:
    return assign_layer(path), assign_domain(path)


def _title(raw: str) -> str:
    cleaned = raw.replace("_", " ").replace("-", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else "Root"


# --------------------------------------------------------------------------- #
# Tree walk
# --------------------------------------------------------------------------- #


def iter_repo_files(root: Path) -> Iterable[tuple[str, Path]]:
    """Yield (posix-rel, abs) for source + manifests + env examples."""
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        parts = [p for p in rel.split("/") if p]
        if any(p in SKIP_DIR_NAMES for p in parts[:-1]):
            continue
        # Product clone volume — never treat nested clones as this repo's code.
        if len(parts) >= 2 and parts[0] == "data" and parts[1] == "clones":
            continue
        name = parts[-1] if parts else path.name
        ext = path.suffix.lower()
        if (
            ext in DETECTED_EXTENSIONS
            or name in _MANIFEST_NAMES
            or name in _ENV_NAMES
            or name.endswith(".toml")
        ):
            yield rel, path


def scan_tree(root: str | Path, *, max_files: int = 4000) -> UnderstandingFacts:
    """Walk a clone and return Layer A facts. Never raises."""
    root_p = Path(root)
    facts = UnderstandingFacts()
    lang_counts: Counter[str] = Counter()
    folder_counts: Counter[str] = Counter()
    import_hits: list[str] = []
    env_text = ""
    seen = 0

    for rel, abs_path in iter_repo_files(root_p):
        seen += 1
        if seen > max_files:
            break
        name = abs_path.name
        try:
            raw = abs_path.read_bytes()
        except OSError:
            continue
        text = raw.decode("utf-8", errors="replace")

        if name in _MANIFEST_NAMES:
            mf = parse_manifest(rel, text)
            if mf:
                facts.manifests.append(mf)
                import_hits.extend(mf.dependencies)
            continue

        if name in _ENV_NAMES:
            env_text += "\n" + text
            continue

        lang = detect_language(rel)
        if lang:
            loc = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
            facts.loc += loc
            facts.file_count += 1
            key = "typescript" if lang in {"typescript", "tsx"} else lang
            lang_counts[key] += 1
            segs = [p for p in rel.split("/") if p]
            if segs:
                folder_counts["/".join(segs[:2] if len(segs) > 1 else segs[:1])] += 1

            facts.endpoints.extend(extract_endpoints_from_source(rel, text))
            facts.entry_points.extend(detect_entry_points_in_file(rel, text))
            facts.jobs.extend(extract_jobs_from_source(rel, text))
            import_hits.extend(_import_names(text, lang))

    facts.languages = dict(lang_counts.most_common())
    facts.folders = [f for f, _ in folder_counts.most_common(24)]
    facts.frameworks = _frameworks_from(import_hits, facts.manifests)
    facts.externals = detect_externals(import_hits, env_text)
    facts.endpoints = _dedupe_endpoints(facts.endpoints)
    facts.entry_points = _dedupe_entry_points(facts.entry_points)
    facts.jobs = _dedupe_jobs(facts.jobs)
    return facts


# --------------------------------------------------------------------------- #
# Manifests / imports / externals
# --------------------------------------------------------------------------- #


def parse_manifest(path: str, text: str) -> ManifestFact | None:
    name = Path(path).name.lower()
    try:
        if name == "package.json":
            data = json.loads(text or "{}")
            deps = {
                **(data.get("dependencies") or {}),
                **(data.get("devDependencies") or {}),
            }
            names = sorted(deps)
            langs = ["TypeScript"] if any("typescript" in n for n in names) else ["JavaScript"]
            frames = [label for key, label in _FRAMEWORK_IMPORTS.items() if _pkg_hit(key, names)]
            return ManifestFact(path, "node", langs, frames, names[:80])
        if name == "pyproject.toml":
            deps = _toml_dep_names(text)
            frames = [label for key, label in _FRAMEWORK_IMPORTS.items() if _pkg_hit(key, deps)]
            return ManifestFact(path, "python", ["Python"], frames, deps[:80])
        if name.startswith("requirements"):
            deps = []
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if not line or line.startswith("-"):
                    continue
                deps.append(re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip().lower())
            frames = [label for key, label in _FRAMEWORK_IMPORTS.items() if _pkg_hit(key, deps)]
            return ManifestFact(path, "python", ["Python"], frames, deps[:80])
        if name == "go.mod":
            return ManifestFact(path, "go", ["Go"], [], [])
        if name == "cargo.toml":
            return ManifestFact(path, "rust", ["Rust"], [], [])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def _toml_dep_names(text: str) -> list[str]:
    names: list[str] = []
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_deps = "dependencies" in stripped.lower()
            continue
        if in_deps and "=" in stripped:
            names.append(stripped.split("=", 1)[0].strip().strip("\"'").lower())
    return names


def _pkg_hit(key: str, names: Iterable[str]) -> bool:
    key = key.lower()
    for n in names:
        n = n.lower()
        if n == key or n.startswith(f"{key}/") or n.startswith(f"{key}-") or n.endswith(f"-{key}"):
            return True
        if key in n and key in {"next", "react", "fastapi", "express"}:
            return True
    return False


def _import_names(text: str, lang: str) -> list[str]:
    names: list[str] = []
    if lang == "python":
        for m in re.finditer(r"^(?:from|import)\s+([A-Za-z0-9_\.]+)", text, re.M):
            names.append(m.group(1).split(".")[0].lower())
            names.append(m.group(1).lower())
    else:
        for m in re.finditer(
            r"""(?:from|import)\s+['"]([^'"]+)['"]""",
            text,
        ):
            names.append(m.group(1).lower())
    return names


def detect_externals(import_hits: Iterable[str], env_text: str = "") -> list[ExternalFact]:
    found: dict[str, ExternalFact] = {}

    def add(name: str, kind: str, evidence: str, confidence: float) -> None:
        cur = found.get(name)
        if cur is None:
            found[name] = ExternalFact(name, kind, [evidence], confidence)
        else:
            if evidence not in cur.evidence:
                cur.evidence.append(evidence)
            cur.confidence = max(cur.confidence, confidence)

    for hit in import_hits:
        h = hit.lower()
        for key, (name, kind) in _SDK_CATALOG.items():
            if h == key or h.startswith(f"{key}.") or h.startswith(f"{key}/"):
                add(name, kind, f"import {hit}", 0.75)

    for pat, name, kind in _ENV_HINTS:
        if pat.search(env_text):
            add(name, kind, "env example", 0.55)

    # Generic HTTP clients are noisy unless they are the only signal.
    if "HTTP client" in found and len(found) > 2:
        found.pop("HTTP client", None)
    return sorted(found.values(), key=lambda e: (-e.confidence, e.name))


def _frameworks_from(import_hits: Iterable[str], manifests: list[ManifestFact]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    pool = list(import_hits)
    for mf in manifests:
        pool.extend(mf.dependencies)
        for f in mf.frameworks:
            if f not in seen:
                seen.add(f)
                out.append(f)
    for hit in pool:
        h = hit.lower()
        for key, label in _FRAMEWORK_IMPORTS.items():
            if (h == key or h.startswith(f"{key}/") or f"/{key}" in h) and label not in seen:
                seen.add(label)
                out.append(label)
    return out


# --------------------------------------------------------------------------- #
# Entry points + endpoints
# --------------------------------------------------------------------------- #


def detect_entry_points_in_file(path: str, text: str) -> list[EntryPointFact]:
    p = normalize_path(path)
    low = p.lower()
    name = p.rsplit("/", 1)[-1]
    out: list[EntryPointFact] = []

    if name in {"main.py", "__main__.py", "app.py", "wsgi.py", "asgi.py"}:
        kind = "http_app" if _FASTAPI_APP.search(text) or _CREATE_APP.search(text) else "cli"
        out.append(EntryPointFact(p, kind, name, "filename convention"))
    if _CELERY_APP.search(text) or "celery" in low and name.endswith(".py"):
        if "celery" in name.lower() or "celery_app" in text:
            out.append(EntryPointFact(p, "worker", name, "celery app"))
    if name == "page.tsx" or name == "page.jsx":
        out.append(EntryPointFact(p, "page", _next_route_from_file(p) or "/", "Next.js page"))
    if name == "route.ts" or name == "route.js":
        out.append(EntryPointFact(p, "http_app", _next_route_from_file(p) or "/", "Next.js route"))
    if "webhook" in low:
        out.append(EntryPointFact(p, "webhook", name, "webhook path"))
        for m in re.finditer(r"^(?:async\s+)?def\s+(?P<name>\w+)", text, re.M):
            fn = m.group("name")
            if not fn.startswith("_"):
                out.append(EntryPointFact(p, "webhook", fn, "webhook handler"))
    if name in {"main.tsx", "main.jsx", "index.tsx"} and ("src/" in low or "app/" in low):
        out.append(EntryPointFact(p, "page", name, "frontend entry"))
    return out


def extract_jobs_from_source(path: str, text: str) -> list[JobFact]:
    """Celery / shared_task handlers and functions in webhook files."""
    p = normalize_path(path)
    out: list[JobFact] = []
    for m in _CELERY_TASK_DEF.finditer(text):
        out.append(JobFact(name=m.group("name"), path=p, kind="celery"))
    if "webhook" in p.lower():
        for m in re.finditer(r"^(?:async\s+)?def\s+(?P<name>\w+)", text, re.M):
            fn = m.group("name")
            if fn.startswith("_"):
                continue
            out.append(JobFact(name=fn, path=p, kind="webhook"))
    return out


def extract_endpoints_from_source(path: str, text: str) -> list[EndpointFact]:
    p = normalize_path(path)
    out: list[EndpointFact] = []
    auth = "session" if _AUTH_HINT.search(text) else "unknown"

    for m in _DECORATOR_ROUTE.finditer(text):
        out.append(
            EndpointFact(
                method=m.group("method").upper(),
                path=_norm_route(m.group("path")),
                file_path=p,
                source="decorator",
                auth_hint=auth,
            )
        )
    for m in _FLASK_ROUTE.finditer(text):
        methods = re.findall(r"['\"](GET|POST|PUT|DELETE|PATCH)['\"]", m.group("rest") or "", re.I)
        for method in methods or ["GET"]:
            out.append(
                EndpointFact(
                    method=method.upper(),
                    path=_norm_route(m.group("path")),
                    file_path=p,
                    source="decorator",
                    auth_hint=auth,
                )
            )
    for m in _EXPRESS_ROUTE.finditer(text):
        out.append(
            EndpointFact(
                method=m.group("method").upper(),
                path=_norm_route(m.group("path")),
                file_path=p,
                source="decorator",
                auth_hint=auth,
            )
        )

    name = p.rsplit("/", 1)[-1]
    if name in {"route.ts", "route.js", "route.tsx"}:
        route = _next_route_from_file(p)
        if route is not None:
            methods = [m.group("method").upper() for m in _NEXT_HANDLER.finditer(text)] or ["GET"]
            for method in methods:
                out.append(
                    EndpointFact(
                        method=method,
                        path=route,
                        file_path=p,
                        source="file_convention",
                        auth_hint=auth,
                    )
                )
    return out


def parse_route_edge_name(dst_name: str) -> tuple[str, str] | None:
    """Parse `GET /health` from a dynamic route edge."""
    text = (dst_name or "").strip()
    m = re.match(r"^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|WEBSOCKET)\s+(/\S*)$", text, re.I)
    if not m:
        return None
    return m.group(1).upper(), _norm_route(m.group(2))


def group_endpoints(endpoints: list[EndpointFact]) -> dict[str, list[EndpointFact]]:
    grouped: dict[str, list[EndpointFact]] = {}
    for ep in endpoints:
        segs = [s for s in ep.path.split("/") if s and not s.startswith("{")]
        key = (segs[0] if segs else "root").upper()
        grouped.setdefault(key, []).append(ep)
    return grouped


def heuristic_narrative(facts: UnderstandingFacts) -> dict[str, Any]:
    """Layer B fallback that does not call an LLM."""
    langs = [_pretty_lang(k) for k in facts.languages]
    frames = facts.frameworks[:6]
    if langs and frames:
        summary = f"This is a {' / '.join(langs)} repository using {', '.join(frames)}."
    elif langs:
        summary = f"This is a {' / '.join(langs)} repository."
    else:
        summary = "Repository indexed. Open a folder on the architecture map to explore."

    domains: list[dict[str, str]] = []
    seen: set[str] = set()
    for folder in facts.folders:
        sample = folder + "/x.py"
        domain = assign_domain(sample)
        if domain in seen or domain == "Tests":
            continue
        seen.add(domain)
        domains.append({"name": domain, "folders": [folder], "why": f"Code under {folder}"})
        if len(domains) >= 8:
            break

    layers = []
    for folder in facts.folders:
        lyr = assign_layer(folder + "/x.py")
        if lyr not in layers and lyr != "test":
            layers.append(lyr)

    questions = [
        "What are the main components?",
        "Where does the application start?",
    ]
    if facts.endpoints:
        questions.append("How does a request get handled?")
        questions.append(f"What does {facts.endpoints[0].method} {facts.endpoints[0].path} do?")
    if facts.externals:
        questions.append("What external services does this use?")
    questions.append("If I change a central module, what breaks?")

    return {
        "summary": summary,
        "domains": domains,
        "architecture_layers": layers,
        "suggested_questions": questions[:6],
    }


def sanitize_narrative(raw: dict[str, Any] | None, facts: UnderstandingFacts) -> dict[str, Any]:
    """Keep Layer B output honest: every domain folder must be in the fact payload."""
    fallback = heuristic_narrative(facts)
    if not isinstance(raw, dict):
        return fallback
    allowed = {normalize_path(f) for f in facts.folders}
    domains: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw.get("domains") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        folders = [normalize_path(str(f)) for f in (item.get("folders") or []) if str(f).strip()]
        folders = [f for f in folders if f in allowed]
        if not name or not folders or name in seen:
            continue
        seen.add(name)
        why = str(item.get("why") or "").strip() or f"Code under {folders[0]}"
        domains.append({"name": name[:64], "folders": folders[:8], "why": why[:240]})
        if len(domains) >= 8:
            break
    if not domains:
        domains = fallback["domains"]
    summary = str(raw.get("summary") or "").strip()
    if len(summary) < 12:
        summary = fallback["summary"]
    layers: list[str] = []
    for lyr in raw.get("architecture_layers") or fallback["architecture_layers"]:
        key = str(lyr).strip().lower()
        if key in LAYER_ORDER and key not in layers and key != "test":
            layers.append(key)
    if not layers:
        layers = list(fallback["architecture_layers"])
    questions = list(fallback["suggested_questions"])
    extra = raw.get("suggested_questions")
    if isinstance(extra, list):
        merged: list[str] = []
        for q in [*extra, *questions]:
            text = str(q).strip()
            if text and text not in merged:
                merged.append(text)
        questions = merged[:6]
    return {
        "summary": summary[:400],
        "domains": domains,
        "architecture_layers": layers,
        "suggested_questions": questions[:6],
    }


def question_tokens(question: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9_]+", (question or "").lower())
        if t not in _STOP_TOKENS and len(t) > 1
    }


def pick_catalog_seed(
    question: str,
    *,
    endpoints: list[EndpointFact],
    jobs: list[JobFact] | None = None,
    retrieved_paths: list[str] | None = None,
) -> CatalogSeed | None:
    """Rank endpoints/jobs for a behavioral question. Prefer HTTP over jobs."""
    q = (question or "").strip()
    if not q:
        return None
    q_low = q.lower()
    tokens = question_tokens(q)
    retrieved = {normalize_path(p) for p in (retrieved_paths or [])}
    candidates: list[CatalogSeed] = []

    for ep in endpoints:
        hay = f"{ep.method} {ep.path} {ep.handler_name or ''} {ep.file_path}"
        score = _overlap_score(tokens, hay)
        blob = f"{ep.method} {ep.path}".lower()
        if blob in q_low or ep.path.lower() in q_low:
            score += 5.0
        if normalize_path(ep.file_path) in retrieved:
            score += 1.5
        if score <= 0:
            continue
        candidates.append(
            CatalogSeed(
                kind="http",
                title=f"{ep.method} {ep.path}",
                score=score + 0.5,
                handler_name=ep.handler_name,
                file_path=ep.file_path,
                method=ep.method,
                path=ep.path,
            )
        )

    for job in jobs or []:
        hay = f"{job.name} {job.path} {job.kind}"
        score = _overlap_score(tokens, hay)
        if job.name.lower() in q_low:
            score += 4.0
        if normalize_path(job.path) in retrieved:
            score += 1.5
        if score <= 0:
            continue
        kind = "job" if job.kind == "celery" else "webhook"
        candidates.append(
            CatalogSeed(
                kind=kind,
                title=job.name,
                score=score,
                handler_name=job.name,
                file_path=job.path,
            )
        )

    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c.score, 0 if c.kind == "http" else 1, c.title))
    return candidates[0]


def _overlap_score(tokens: set[str], haystack: str) -> float:
    hay = set(re.findall(r"[a-z0-9_]+", (haystack or "").lower()))
    if not tokens or not hay:
        return 0.0
    return float(len(tokens & hay))


def _pretty_lang(key: str) -> str:
    return {
        "python": "Python",
        "javascript": "JavaScript",
        "typescript": "TypeScript",
        "tsx": "TypeScript",
    }.get(key, key.title())


def _next_route_from_file(path: str) -> str | None:
    p = normalize_path(path)
    marker = "/app/"
    idx = p.find(marker)
    if idx < 0:
        return None
    rest = p[idx + len(marker) :]
    segs = [s for s in rest.split("/") if s and s not in {"page.tsx", "page.jsx", "route.ts", "route.js", "route.tsx"}]
    cleaned: list[str] = []
    for s in segs:
        if s.startswith("(") and s.endswith(")"):
            continue
        if s.startswith("@"):
            continue
        cleaned.append(s.replace("[", "{").replace("]", "}"))
    return "/" + "/".join(cleaned) if cleaned else "/"


def _norm_route(path: str) -> str:
    path = path.strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def _dedupe_endpoints(items: list[EndpointFact]) -> list[EndpointFact]:
    seen: set[tuple[str, str, str]] = set()
    out: list[EndpointFact] = []
    for ep in items:
        key = (ep.method, ep.path, ep.file_path)
        if key in seen:
            continue
        seen.add(key)
        out.append(ep)
    return out


def _dedupe_entry_points(items: list[EntryPointFact]) -> list[EntryPointFact]:
    seen: set[tuple[str, str]] = set()
    out: list[EntryPointFact] = []
    for ep in items:
        key = (ep.path, ep.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(ep)
    return out


def _dedupe_jobs(items: list[JobFact]) -> list[JobFact]:
    seen: set[tuple[str, str, str]] = set()
    out: list[JobFact] = []
    for job in items:
        key = (job.kind, job.name, job.path)
        if key in seen:
            continue
        seen.add(key)
        out.append(job)
    return out
