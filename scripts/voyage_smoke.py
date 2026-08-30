"""Live smoke test for the Voyage embedding provider — reads .env, makes one real call.

    python scripts/voyage_smoke.py

Embeds a few code-ish strings via the configured EMBEDDING_* settings, then prints
the model, the returned dimension (must match EMBEDDING_DIMS / the chunks vector
column), and a cosine sanity check: a related pair should score higher than an
unrelated pair. Run this before switching a repo over to real embeddings. Not part
of pytest (needs network + a Voyage key).
"""

from __future__ import annotations

import asyncio
import math

from repo_core.config import get_settings
from repo_core.models import EMBEDDING_DIMS
from repo_providers.factory import get_embedding_provider


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def main() -> None:
    settings = get_settings()
    print(f"EMBEDDING_PROVIDER={settings.embedding_provider!r} "
          f"model={settings.embedding_model!r} dims={settings.embedding_dims}")
    if (settings.embedding_provider or "").lower() not in {"voyage"}:
        print(
            "\n⚠  EMBEDDING_PROVIDER is not 'voyage' — this will smoke the MOCK embedder.\n"
            "   Set EMBEDDING_PROVIDER=voyage and EMBEDDING_API_KEY=<key> in .env first."
        )

    embedder = get_embedding_provider(settings)
    print(f"embedder model: {embedder.model}\n")

    docs = [
        "def slugify(value): return value.lower().replace(' ', '-')",
        "function slugify(s) { return s.toLowerCase().replace(/ /g, '-'); }",
        "SELECT * FROM invoices WHERE status = 'overdue' ORDER BY due_date;",
    ]
    vectors = await embedder.embed(docs, input_type="document")
    dim = len(vectors[0]) if vectors else 0
    print(f"returned {len(vectors)} vectors, dimension = {dim}")
    if dim != EMBEDDING_DIMS:
        print(
            f"⚠  dimension {dim} != EMBEDDING_DIMS {EMBEDDING_DIMS} — the chunks.embedding "
            f"column is vector({EMBEDDING_DIMS}); set EMBEDDING_DIMS to match or the store will pad/truncate."
        )

    related = _cosine(vectors[0], vectors[1])   # slugify py vs slugify js
    unrelated = _cosine(vectors[0], vectors[2])  # slugify vs SQL
    print(f"\ncosine(related python/js slugify) = {related:.3f}")
    print(f"cosine(unrelated slugify/SQL)     = {unrelated:.3f}")
    print("OK — related > unrelated" if related > unrelated else "⚠  related !> unrelated (unexpected)")


if __name__ == "__main__":
    asyncio.run(main())
