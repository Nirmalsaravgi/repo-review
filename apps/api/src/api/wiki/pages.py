"""Assemble wiki pages from stored summaries. This module does not call an LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from api.synth.text import question_set
from api.wiki.diagram import drop_unknown, ir_to_mermaid, module_diagram, validate_diagram

_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class PageDraft:
    type: str
    slug: str
    title: str
    parent_slug: str | None
    body: str
    diagram_ir: dict[str, Any] | None = None
    depends_on: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)


def assemble_pages(
    *,
    style_label: str,
    overview: str,
    categories: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    features: list[dict[str, Any]],
    endpoints: list[dict[str, Any]],
    indexed_sha: str | None,
    known_files: set[str],
) -> list[PageDraft]:
    enabled = {item["key"] for item in categories if item.get("enabled")}
    pages: list[PageDraft] = []
    module_pages = _module_pages(modules, known_files)
    feature_pages = _feature_pages(features, known_files) if "execution_flows" in enabled else []

    leaves = [m for m in modules if m.get("kind") == "module"]
    questions = question_set([m["name"] for m in leaves[:6]], [f["name"] for f in features[:4]])
    overview_body = _overview_body(style_label, overview, leaves, features, questions, indexed_sha)
    pages.append(
        PageDraft(
            type="overview",
            slug="overview",
            title="Overview",
            parent_slug=None,
            body=overview_body,
            depends_on=[f"module:{m['id']}" for m in leaves],
            sources=_sources_from_modules(leaves, known_files),
        )
    )

    arch_ir = module_diagram(modules)
    known_refs = {f"module:{m['id']}" for m in modules}
    arch_ir = drop_unknown(arch_ir, known_refs)
    pages.append(
        PageDraft(
            type="architecture",
            slug="architecture",
            title="Architecture",
            parent_slug="overview",
            body=_architecture_body(module_pages),
            diagram_ir=arch_ir,
            depends_on=[node["ref"] for node in arch_ir["nodes"]],
            sources=_sources_from_modules(leaves, known_files),
        )
    )
    pages.extend(module_pages)
    pages.extend(feature_pages)

    if "api" in enabled and endpoints:
        pages.append(_api_page(endpoints, known_files))
    if "data" in enabled:
        pages.append(_note_page("data", "Data", "architecture", _data_body(modules), known_files, modules))
    if "security" in enabled:
        pages.append(_note_page("security", "Security", "architecture", _security_body(endpoints), known_files, modules))
    if "testing" in enabled:
        pages.append(
            PageDraft(
                type="testing",
                slug="testing",
                title="Testing",
                parent_slug="overview",
                body="Tests are present in this repository. They are evidence for behavior, not a separate architecture.",
                depends_on=[],
            )
        )
    if "deploy" in enabled:
        pages.append(
            PageDraft(
                type="deploy",
                slug="deploy",
                title="Deploy",
                parent_slug="overview",
                body="Docker or CI signals were detected. Deployment units are boundaries, not the module tree.",
                depends_on=[],
            )
        )
    for key, title in (
        ("public_api", "Public API"),
        ("extension_guide", "Extension guide"),
        ("page_map", "Pages"),
        ("component_map", "Components"),
        ("commands", "Commands"),
        ("events", "Events"),
        ("pipeline", "Pipeline"),
        ("agent", "Agents"),
        ("tool", "Tools"),
        ("prompt", "Prompts"),
        ("retrieval", "Retrieval"),
        ("eval", "Evaluation"),
    ):
        if key in enabled:
            pages.append(
                PageDraft(
                    type=key,
                    slug=key.replace("_", "-"),
                    title=title,
                    parent_slug="overview",
                    body=f"{title} is in the documentation plan because the repository profile is {style_label}.",
                    depends_on=[],
                )
            )

    diagram = PageDraft(
        type="diagram",
        slug="diagram/architecture",
        title="Architecture diagram",
        parent_slug="architecture",
        body="Rendered from the module list. Every node id is a module in this repository.",
        diagram_ir=arch_ir,
        depends_on=[node["ref"] for node in arch_ir["nodes"]],
    )
    _ = validate_diagram(arch_ir, known_refs=known_refs)
    pages.append(diagram)
    return pages


def _module_pages(modules: list[dict[str, Any]], known_files: set[str]) -> list[PageDraft]:
    by_id = {m["id"]: m for m in modules}
    pages: list[PageDraft] = []
    for module in modules:
        if module.get("kind") == "application":
            continue
        parent = by_id.get(module.get("parent_id") or "")
        if parent and parent.get("kind") != "application":
            parent_slug = _module_slug(parent)
        else:
            parent_slug = "architecture"
        files = [path for path in (module.get("files") or []) if path in known_files]
        depends = [f"module:{module['id']}"] + [f"file:{path}" for path in files]
        summary = (module.get("summary") or "").strip() or f"{module['name']} has no summary yet."
        confidence = module.get("cohesion")
        body = (
            f"{summary}\n\n"
            f"Kind: {module.get('kind')}. Cohesion {float(module.get('cohesion') or 0):.2f}. "
            f"Coupling {float(module.get('coupling') or 0):.2f}.\n\n"
            "## Source\n"
            + ("\n".join(f"- `{path}`" for path in files[:16]) or "- No source files recorded.")
        )
        pages.append(
            PageDraft(
                type="module",
                slug=_module_slug(module),
                title=str(module["name"]),
                parent_slug=parent_slug,
                body=body,
                depends_on=depends,
                sources=[{"path": path} for path in files],
            )
        )
        _ = confidence
    return pages


def _feature_pages(features: list[dict[str, Any]], known_files: set[str]) -> list[PageDraft]:
    pages: list[PageDraft] = []
    for feature in features:
        files = [path for path in (feature.get("files") or []) if path in known_files]
        summary = (feature.get("summary") or "").strip() or f"{feature['name']} has no summary yet."
        resolution = (feature.get("evidence") or {}).get("resolution") or "call_graph"
        body = (
            f"{summary}\n\n"
            f"Resolution: {resolution}. Confidence {float(feature.get('confidence') or 0):.2f}. "
            "Steps at or above 0.7 are confirmed call-graph edges. Lower scores and string-literal "
            "event joins are inferred.\n\n"
            "## Source\n"
            + ("\n".join(f"- `{path}`" for path in files[:16]) or "- No source files recorded.")
        )
        pages.append(
            PageDraft(
                type="feature",
                slug="feature/" + _slug(str(feature["name"])) + "-" + str(feature["id"])[:8],
                title=str(feature["name"])[:180],
                parent_slug="architecture",
                body=body,
                depends_on=[f"feature:{feature['id']}"] + [f"file:{path}" for path in files],
                sources=[{"path": path} for path in files],
            )
        )
    return pages


def _api_page(endpoints: list[dict[str, Any]], known_files: set[str]) -> PageDraft:
    lines = []
    sources = []
    for ep in endpoints[:40]:
        path = ep.get("file_path") or ""
        auth = ep.get("auth_hint") or "unknown"
        lines.append(f"- `{ep.get('method')} {ep.get('path')}` ({auth})" + (f" — `{path}`" if path else ""))
        if path and path in known_files:
            sources.append({"path": path})
    return PageDraft(
        type="api",
        slug="api",
        title="HTTP API",
        parent_slug="overview",
        body="Endpoints extracted from the source. Auth hints are what the extractor saw, not a review.\n\n" + "\n".join(lines),
        depends_on=[f"file:{item['path']}" for item in sources],
        sources=sources,
    )


def _note_page(
    type_: str,
    title: str,
    parent: str,
    body: str,
    known_files: set[str],
    modules: list[dict[str, Any]],
) -> PageDraft:
    sources = _sources_from_modules([m for m in modules if m.get("kind") == "module"], known_files)[:8]
    return PageDraft(
        type=type_,
        slug=type_,
        title=title,
        parent_slug=parent,
        body=body,
        depends_on=[f"file:{item['path']}" for item in sources],
        sources=sources,
    )


def _overview_body(
    style_label: str,
    overview: str,
    modules: list[dict[str, Any]],
    features: list[dict[str, Any]],
    questions: list[str],
    indexed_sha: str | None,
) -> str:
    module_lines = "\n".join(f"- [{m['name']}](wiki:{_module_slug(m)})" for m in modules[:12]) or "- None yet."
    feature_lines = "\n".join(f"- {f['name']}" for f in features[:8]) or "- None detected."
    question_lines = "\n".join(f"- {q}" for q in questions)
    sha = indexed_sha or "unpinned"
    return (
        f"{overview}\n\n"
        f"Profile: {style_label}. Indexed at `{sha}`.\n\n"
        f"## Modules\n{module_lines}\n\n"
        f"## Features\n{feature_lines}\n\n"
        f"## Questions this wiki should answer\n{question_lines}\n"
    )


def _architecture_body(module_pages: list[PageDraft]) -> str:
    lines = "\n".join(f"- [{page.title}](wiki:{page.slug})" for page in module_pages) or "- No modules."
    return "Responsibility modules. Grouping comes from the dependency graph, with cohesion and coupling on each page.\n\n" + lines


def _data_body(modules: list[dict[str, Any]]) -> str:
    names = [m["name"] for m in modules if m.get("kind") == "module"]
    if not names:
        return "A data analyzer was selected, but no responsibility module is recorded yet."
    return "Data is in scope because a database analyzer or model files were detected. Related modules: " + ", ".join(names[:8]) + "."


def _security_body(endpoints: list[dict[str, Any]]) -> str:
    guarded = [ep for ep in endpoints if (ep.get("auth_hint") or "unknown") not in {"unknown", ""}]
    if not guarded:
        return "Security is in the plan, but no endpoint carried an auth hint."
    lines = "\n".join(f"- `{ep.get('method')} {ep.get('path')}` hint `{ep.get('auth_hint')}`" for ep in guarded[:20])
    return "Auth hints found on endpoints. These are extractor signals, not a proof of enforcement.\n\n" + lines


def _sources_from_modules(modules: list[dict[str, Any]], known_files: set[str]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for module in modules:
        for path in module.get("files") or []:
            if path in known_files and path not in seen:
                seen.add(path)
                sources.append({"path": path})
    return sources


def _module_slug(module: dict[str, Any]) -> str:
    return "module/" + _slug(str(module.get("name") or "module")) + "-" + str(module["id"])[:8]


def _slug(text: str) -> str:
    slug = _SLUG.sub("-", text.lower()).strip("-")
    return slug[:80] or "page"


def diagram_mermaid(ir: dict[str, Any] | None, known_refs: set[str]) -> str | None:
    if not ir:
        return None
    cleaned = drop_unknown(ir, known_refs)
    if not cleaned["nodes"]:
        return None
    return ir_to_mermaid(cleaned)
