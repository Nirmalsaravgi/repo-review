"""In-repo bot config (`.repo-review.yml`) — untrusted, parsed with safe defaults.

The config file lives in the customer's repository, so it is attacker-authored
data. It may only *tighten or relax which checks run and their thresholds* — it
can never inject anything into an LLM prompt or turn posting on. Unknown keys are
ignored, values are coerced and clamped, and a missing/malformed file yields the
defaults. `parse_review_config` never raises.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import yaml

# The check identifiers the bot knows about. Anything else in the file is ignored.
CHECK_NAMES = ("blast_radius", "duplicate", "missing_wrapper", "pattern_consistency")

# Filenames tried in order at the repo root.
CONFIG_FILENAMES = (".repo-review.yml", ".repo-review.yaml", ".github/repo-review.yml")


@dataclass(frozen=True, slots=True)
class ReviewConfig:
    """Resolved, clamped review configuration.

    `enabled` is the repo-level master switch (independent of the server-side
    `pr_bot_enabled`, which gates actual posting). `checks` maps every known
    check to an on/off flag.
    """

    enabled: bool = True
    min_confidence: float = 0.75
    max_comments: int = 3
    checks: dict[str, bool] = field(
        default_factory=lambda: {name: True for name in CHECK_NAMES}
    )

    def check_enabled(self, name: str) -> bool:
        return self.enabled and self.checks.get(name, False)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_float(value: Any, default: float, *, lo: float, hi: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(f):
        return default
    return max(lo, min(hi, f))


def _as_int(value: Any, default: int, *, lo: int, hi: int) -> int:
    try:
        i = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, i))


def parse_review_config(
    text: str | None,
    *,
    default_min_confidence: float = 0.75,
    default_max_comments: int = 3,
) -> ReviewConfig:
    """Parse a `.repo-review.yml` body into a clamped `ReviewConfig`.

    A `None`/empty/malformed body, or one whose top level isn't a mapping,
    returns defaults. Server-provided defaults seed thresholds so a deployment
    can be conservative even when a repo ships no file.
    """
    base = ReviewConfig(
        min_confidence=_as_float(default_min_confidence, 0.75, lo=0.0, hi=1.0),
        max_comments=_as_int(default_max_comments, 3, lo=0, hi=20),
    )
    if not text or not text.strip():
        return base
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return base
    if not isinstance(data, dict):
        return base

    enabled = _as_bool(data.get("enabled"), base.enabled)
    min_conf = _as_float(
        data.get("min_confidence"), base.min_confidence, lo=0.0, hi=1.0
    )
    max_comments = _as_int(data.get("max_comments"), base.max_comments, lo=0, hi=20)

    checks = {name: True for name in CHECK_NAMES}
    raw_checks = data.get("checks")
    if isinstance(raw_checks, dict):
        for name in CHECK_NAMES:
            if name in raw_checks:
                checks[name] = _as_bool(raw_checks[name], True)

    return ReviewConfig(
        enabled=enabled,
        min_confidence=min_conf,
        max_comments=max_comments,
        checks=checks,
    )


def load_config_text(root: Any) -> str | None:
    """Read the first present config file under a clone root (`pathlib.Path`)."""
    from pathlib import Path

    root_path = Path(root)
    for name in CONFIG_FILENAMES:
        candidate = root_path / name
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None
