"""Labeled eval dataset: questions with expected files / strings per category."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Phase 0 categories (implementation-plan.md §4, Week 4).
CATEGORIES = ("locate", "flow", "exact_string", "unanswerable")


@dataclass
class EvalItem:
    id: str
    category: str
    question: str
    expected_files: list[str] = field(default_factory=list)
    expected_strings: list[str] = field(default_factory=list)


@dataclass
class Dataset:
    root: str  # agent root, relative to the project root
    items: list[EvalItem]


def load_dataset(path: str | Path) -> Dataset:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = [EvalItem(**item) for item in data["items"]]
    unknown = {i.category for i in items} - set(CATEGORIES)
    if unknown:
        raise ValueError(f"Unknown categories in dataset: {sorted(unknown)}")
    return Dataset(root=data.get("root", "."), items=items)
