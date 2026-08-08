"""Sample module for Phase 2 parser tests."""

from __future__ import annotations

import json


class Greeter:
    """Says hello."""

    def hello(self, name: str) -> str:
        """Return a greeting."""
        return f"hi {name}"


def standalone(x: int) -> int:
    return x + 1
