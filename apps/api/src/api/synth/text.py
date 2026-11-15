"""Deterministic summaries. Every sentence is assembled from supplied evidence."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_PATH = re.compile(r"\b[\w./\\-]+\.(?:py|ts|tsx|js|jsx|go|rs|toml|json|yml|yaml)\b")


def content_sha(*parts: str) -> str:
    payload = "\n".join(parts)
    return hashlib.sha1(payload.encode()).hexdigest()


def should_skip(existing_summary: str | None, existing_sha: str | None, new_sha: str) -> bool:
    return bool(existing_summary and existing_summary.strip() and existing_sha and existing_sha == new_sha)


def ungrounded_paths(text: str, allowed: set[str]) -> list[str]:
    """Paths mentioned in prose that are not in the evidence set."""
    allowed_norm = {p.replace("\\", "/") for p in allowed}
    bad: list[str] = []
    for match in _PATH.findall(text or ""):
        norm = match.replace("\\", "/")
        if norm in allowed_norm:
            continue
        if any(norm.endswith("/" + item) or item.endswith("/" + norm) for item in allowed_norm):
            continue
        bad.append(norm)
    return bad


def leaf_summary(
    name: str,
    files: list[str],
    cohesion: float,
    coupling: float,
    atomic_lines: list[str],
    *,
    deep: bool,
) -> str:
    shown = ", ".join(files[:4]) or "no indexed files"
    if not deep:
        return f"{name} covers {shown}."
    extra = ""
    if atomic_lines:
        extra = " Members: " + "; ".join(line.strip().rstrip(".") for line in atomic_lines[:3]) + "."
    return (
        f"{name} covers {shown}. "
        f"Cohesion {cohesion:.2f}, coupling {coupling:.2f}.{extra}"
    )


def parent_summary(name: str, children: list[tuple[str, str]]) -> str:
    """Built only from child summaries, so a parent cannot invent a leaf."""
    if not children:
        return f"{name} has no child modules."
    bits = []
    for child_name, child_summary in children:
        sentence = (child_summary or "").strip().split(".")[0].strip()
        bits.append(f"{child_name} ({sentence})" if sentence else child_name)
    return f"{name} brings together " + "; ".join(bits) + "."


def feature_summary(
    name: str,
    *,
    entry_file: str | None,
    hops: list[dict[str, Any]],
    modules: list[str],
    resolution: str,
) -> str:
    confirmed = [hop for hop in hops if float(hop.get("confidence") or 0) >= 0.7]
    inferred = [hop for hop in hops if float(hop.get("confidence") or 0) < 0.7]
    def _fmt(items: list[dict[str, Any]]) -> str:
        if not items:
            return "none"
        return ", ".join(f"{item.get('src')} → {item.get('dst')}" for item in items[:6])

    where = f" in {entry_file}" if entry_file else ""
    touched = ", ".join(modules) if modules else "no other module"
    return (
        f"{name} starts{where}. "
        f"Confirmed steps ({resolution}): {_fmt(confirmed)}. "
        f"Inferred steps: {_fmt(inferred)}. "
        f"Touches {touched}."
    )


def overview_summary(style_label: str, children: list[tuple[str, str]], features: list[str]) -> str:
    if not children:
        base = f"This {style_label} does not have responsibility modules yet."
    else:
        bits = []
        for child_name, child_summary in children[:4]:
            sentence = (child_summary or "").strip().split(".")[0].strip()
            bits.append(f"{child_name}: {sentence}" if sentence else child_name)
        base = f"This {style_label} is explained from its modules — " + "; ".join(bits) + "."
    if features:
        base += " Cross-cutting features: " + ", ".join(features[:4]) + "."
    return base[:700]


def atomic_stub(
    *,
    name: str,
    kind: str,
    path: str,
    signature: str | None,
    docstring: str | None,
    start_line: int | None,
    end_line: int | None,
    indexed_sha: str | None,
) -> dict[str, Any]:
    first = ""
    if docstring:
        first = docstring.strip().split("\n", 1)[0].strip().strip("\"'")
    if first:
        summary = f"{name} ({kind}) in {path}: {first}"
        confidence = 0.62
    elif signature:
        summary = f"{name} is a {kind} in {path}, signature {signature.strip()[:180]}."
        confidence = 0.5
    else:
        summary = f"{name} is a {kind} in {path}."
        confidence = 0.4
    evidence = [
        {
            "file": path,
            "lines": [start_line or 1, end_line or start_line or 1],
            "sha": indexed_sha,
        }
    ]
    return {
        "summary": summary[:500],
        "facts": {
            "responsibility": summary[:240],
            "inputs": [signature.strip()[:180]] if signature else [],
            "outputs": [],
            "side_effects": [],
        },
        "inferences": [],
        "evidence": evidence,
        "confidence": confidence,
    }


def question_set(module_names: list[str], feature_names: list[str]) -> list[str]:
    questions = ["What are the main modules, and how do they relate?"]
    for name in feature_names[:4]:
        questions.append(f"How does {name} work, and which modules does it cross?")
    for name in module_names[:3]:
        questions.append(f"What is {name} responsible for?")
    return questions[:8]
