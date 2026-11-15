"""Diagrams are a typed IR checked against the graph, then rendered."""

from __future__ import annotations

import re
from typing import Any

_SAFE = re.compile(r"[^A-Za-z0-9_]")


def diagram_ir(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    boundaries: list[str] | None = None,
) -> dict[str, Any]:
    ordering = [node["id"] for node in nodes]
    return {
        "nodes": nodes,
        "edges": edges,
        "ordering": ordering,
        "boundaries": list(boundaries or []),
    }


def validate_diagram(ir: dict[str, Any], *, known_refs: set[str]) -> list[dict[str, Any]]:
    """Fail any node whose ref is not in the graph, and any edge with a dangling end."""
    checks: list[dict[str, Any]] = []
    node_ids = {node.get("id") for node in ir.get("nodes") or []}
    for node in ir.get("nodes") or []:
        ref = str(node.get("ref") or "")
        status = "pass" if ref in known_refs else "fail"
        checks.append({"check_type": "diagram_ref", "status": status, "detail": {"ref": ref, "id": node.get("id")}})
    for edge in ir.get("edges") or []:
        src, dst = edge.get("src"), edge.get("dst")
        ok = src in node_ids and dst in node_ids
        checks.append(
            {
                "check_type": "edge_exists",
                "status": "pass" if ok else "fail",
                "detail": {"src": src, "dst": dst, "kind": edge.get("kind")},
            }
        )
    return checks


def drop_unknown(ir: dict[str, Any], known_refs: set[str]) -> dict[str, Any]:
    """Keep only nodes that resolve, then edges whose ends survived."""
    nodes = [node for node in ir.get("nodes") or [] if str(node.get("ref") or "") in known_refs]
    ids = {node["id"] for node in nodes}
    edges = [edge for edge in ir.get("edges") or [] if edge.get("src") in ids and edge.get("dst") in ids]
    return diagram_ir(nodes, edges, boundaries=ir.get("boundaries") or [])


def ir_to_mermaid(ir: dict[str, Any]) -> str:
    labels = {node["id"]: _mid(index) for index, node in enumerate(ir.get("nodes") or [])}
    lines = ["flowchart TD"]
    for node in ir.get("nodes") or []:
        lines.append(f'    {labels[node["id"]]}["{_esc(str(node.get("label") or node["id"]))}"]')
    for edge in ir.get("edges") or []:
        src, dst = labels.get(edge.get("src")), labels.get(edge.get("dst"))
        if not src or not dst:
            continue
        confidence = float(edge.get("confidence") or 1)
        arrow = "-->" if confidence >= 0.7 else "-.->"
        kind = str(edge.get("kind") or "")
        if kind and kind != "calls":
            lines.append(f"    {src} {arrow}|{_esc(kind)}| {dst}")
        else:
            lines.append(f"    {src} {arrow} {dst}")
    return "\n".join(lines)


def mermaid_from_call_steps(title: str, steps: list[Any], known_ids: set[str]) -> str:
    """Build IR from a call-flow walk, drop unknown symbols, then render."""
    nodes: dict[str, str] = {}
    edges: list[dict[str, Any]] = []

    def _add(symbol_id: Any, label: str) -> str | None:
        if symbol_id is None:
            return None
        key = str(symbol_id)
        if known_ids and key not in known_ids:
            return None
        nodes.setdefault(key, label or key)
        return key

    if title and not steps:
        nodes["entry"] = title
    for step in steps:
        src = _add(getattr(step, "src_id", None), getattr(step, "src_name", ""))
        dst = _add(getattr(step, "dst_id", None), getattr(step, "dst_name", ""))
        if src and dst:
            edges.append(
                {
                    "src": src,
                    "dst": dst,
                    "kind": getattr(step, "kind", "calls"),
                    "confidence": float(getattr(step, "confidence", 1) or 1),
                }
            )
    ir = diagram_ir(
        [{"id": key, "label": label, "ref": f"symbol:{key}"} for key, label in nodes.items() if key != "entry"]
        + ([{"id": "entry", "label": title, "ref": "symbol:entry"}] if "entry" in nodes and not known_ids else []),
        edges,
    )
    known_refs = {f"symbol:{key}" for key in known_ids} if known_ids else {node["ref"] for node in ir["nodes"]}
    if known_ids and "entry" in {node["id"] for node in ir["nodes"]}:
        known_refs.add("symbol:entry")
    ir = drop_unknown(ir, known_refs)
    return ir_to_mermaid(ir)


def module_diagram(modules: list[dict[str, Any]]) -> dict[str, Any]:
    """One node per responsibility module. Edges are not invented."""
    nodes = [
        {"id": item["id"], "label": item["name"], "ref": f"module:{item['id']}"}
        for item in modules
        if item.get("kind") == "module"
    ]
    return diagram_ir(nodes, [], boundaries=[item["name"] for item in modules if item.get("kind") == "service"])


def _mid(index: int) -> str:
    return f"n{index}"


def _esc(text: str) -> str:
    cleaned = _SAFE.sub(" ", text).strip()
    return (cleaned or "node")[:60]
