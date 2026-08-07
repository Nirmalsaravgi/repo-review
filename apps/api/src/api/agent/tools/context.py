"""Shared tool execution context.

Filesystem tools only need `root`. Git / ownership tools also need tenant +
repo ids (and optionally Redis for blame cache).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID


@dataclass
class ToolContext:
    root: Path
    org_id: UUID | None = None
    repo_id: UUID | None = None
    redis: Any | None = None

    @classmethod
    def from_root(cls, root: Path) -> ToolContext:
        return cls(root=root)
