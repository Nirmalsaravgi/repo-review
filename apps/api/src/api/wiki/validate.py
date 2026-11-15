"""Publish gate. A failed check keeps the previous published wiki."""

from __future__ import annotations

from typing import Any

from api.wiki.pages import PageDraft


def validate_pages(
    pages: list[PageDraft],
    *,
    known_files: set[str],
    known_symbols: set[str],
    known_modules: set[str],
    indexed_sha: str | None,
) -> list[dict[str, Any]]:
    slugs = {page.slug for page in pages}
    checks: list[dict[str, Any]] = []
    for page in pages:
        checks.append(
            {
                "slug": page.slug,
                "check_type": "freshness",
                "status": "pass",
                "detail": {"indexed_sha": indexed_sha},
            }
        )
        if page.parent_slug and page.parent_slug not in slugs:
            checks.append(
                {
                    "slug": page.slug,
                    "check_type": "cross_page",
                    "status": "fail",
                    "detail": {"parent_slug": page.parent_slug},
                }
            )
        else:
            checks.append(
                {
                    "slug": page.slug,
                    "check_type": "cross_page",
                    "status": "pass",
                    "detail": {"parent_slug": page.parent_slug},
                }
            )
        for source in page.sources:
            path = source.get("path") or ""
            checks.append(
                {
                    "slug": page.slug,
                    "check_type": "source_path",
                    "status": "pass" if path in known_files else "fail",
                    "detail": {"path": path},
                }
            )
        for ref in page.depends_on:
            kind, _, value = ref.partition(":")
            if kind == "file":
                status = "pass" if value in known_files else "fail"
                check_type = "source_path"
            elif kind == "symbol":
                status = "pass" if value in known_symbols else "fail"
                check_type = "symbol_id"
            elif kind == "module":
                status = "pass" if value in known_modules else "fail"
                check_type = "cross_page"
            elif kind == "feature":
                status = "pass"
                check_type = "cross_page"
            else:
                status = "warn"
                check_type = "cross_page"
            checks.append({"slug": page.slug, "check_type": check_type, "status": status, "detail": {"ref": ref}})
        if page.diagram_ir:
            from api.wiki.diagram import validate_diagram

            known_refs = {f"module:{mid}" for mid in known_modules}
            known_refs |= {f"symbol:{sid}" for sid in known_symbols}
            for item in validate_diagram(page.diagram_ir, known_refs=known_refs):
                item["slug"] = page.slug
                checks.append(item)
        if "Inferred steps" in page.body:
            checks.append(
                {
                    "slug": page.slug,
                    "check_type": "freshness",
                    "status": "warn",
                    "detail": {"note": "page distinguishes inferred steps"},
                }
            )
    return checks


def publish_allowed(checks: list[dict[str, Any]]) -> bool:
    return not any(item.get("status") == "fail" for item in checks)


def status_for(slug: str, checks: list[dict[str, Any]]) -> str:
    mine = [item for item in checks if item.get("slug") == slug]
    if any(item.get("status") == "fail" for item in mine):
        return "fail"
    if any(item.get("status") == "warn" for item in mine):
        return "warn"
    return "pass"
