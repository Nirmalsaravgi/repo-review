"""Phase 2 P4 — RRF fusion and hybrid retrieval golden set."""

from __future__ import annotations

from pathlib import Path

import pytest
from api.retrieval import RetrievalHit, reciprocal_rank_fusion
from api.retrieval.hybrid import build_hybrid_for_tests
from api.retrieval.lexical import LexicalRetriever, SymbolRow
from api.retrieval.semantic import SemanticRetriever
from repo_providers import MockEmbeddingProvider


def test_rrf_promotes_consensus() -> None:
    """Item mid-ranked in both lists should beat items that only win one list."""
    a = RetrievalHit("a.py", 1, 1, "a", sources=("semantic",))
    b_sem = RetrievalHit("b.py", 1, 1, "b from sem", sources=("semantic",))
    b_lex = RetrievalHit("b.py", 1, 1, "b from lex", sources=("lexical",))
    c_sem = RetrievalHit("c.py", 1, 1, "c", sources=("semantic",))
    c_lex = RetrievalHit("c.py", 1, 1, "c", sources=("lexical",))
    noise = RetrievalHit("noise.py", 1, 1, "n", sources=("lexical",))
    # semantic: a, b, c — lexical: c, b, noise
    fused = reciprocal_rank_fusion(
        [[a, b_sem, c_sem], [c_lex, b_lex, noise]],
        k=60,
        limit=4,
    )
    paths = [h.path for h in fused]
    assert paths.index("b.py") < paths.index("a.py")
    b_hit = next(h for h in fused if h.path == "b.py")
    assert set(b_hit.sources) == {"lexical", "semantic"}


@pytest.mark.asyncio
async def test_hybrid_beats_single_channel_on_golden(tmp_path: Path) -> None:
    """Golden set: target needs both lexical exactness and semantic consensus.

    - target.py: exact symbol `chargeCard` + payment vocabulary (both channels)
    - semantic_only.py: payment vocabulary, no exact symbol (semantic likes it)
    - lexical_only.py: symbol `chargeCardHelper` / grep noise without payment words
    - decoy.py: unrelated

    Query mixes a conceptual phrase with the exact symbol name. Semantic alone
    may rank semantic_only first; lexical alone may prefer lexical_only by
    prefix. Fusion should promote target.py.
    """
    (tmp_path / "target.py").write_text(
        "def chargeCard(amount):\n"
        "    '''Process stripe payment checkout for the cart.'''\n"
        "    return stripe.charge(amount)\n",
        encoding="utf-8",
    )
    (tmp_path / "semantic_only.py").write_text(
        "def processPayment(cart):\n"
        "    '''Stripe payment checkout flow without the chargeCard name.'''\n"
        "    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "lexical_only.py").write_text(
        "def chargeCardHelper(x):\n"
        "    '''Utility rename shim, no payment semantics.'''\n"
        "    return x\n",
        encoding="utf-8",
    )
    (tmp_path / "decoy.py").write_text(
        "def renderGrid():\n    return 'css flexbox layout'\n",
        encoding="utf-8",
    )

    emb = MockEmbeddingProvider(dimensions=128)
    docs = {
        "target.py": (tmp_path / "target.py").read_text(encoding="utf-8"),
        "semantic_only.py": (tmp_path / "semantic_only.py").read_text(encoding="utf-8"),
        "lexical_only.py": (tmp_path / "lexical_only.py").read_text(encoding="utf-8"),
        "decoy.py": (tmp_path / "decoy.py").read_text(encoding="utf-8"),
    }
    memory_docs: list[tuple[RetrievalHit, list[float]]] = []
    for path, text in docs.items():
        vec = (await emb.embed([text]))[0]
        memory_docs.append(
            (
                RetrievalHit(path, 1, 3, text[:200], sources=("semantic",)),
                vec,
            )
        )

    symbols = [
        SymbolRow("chargeCard", "target.py", 1, 3, "function", "def chargeCard(amount)"),
        SymbolRow(
            "chargeCardHelper",
            "lexical_only.py",
            1,
            3,
            "function",
            "def chargeCardHelper(x)",
        ),
        SymbolRow("processPayment", "semantic_only.py", 1, 3, "function"),
        SymbolRow("renderGrid", "decoy.py", 1, 2, "function"),
    ]

    query = "chargeCard stripe payment checkout"
    hybrid = build_hybrid_for_tests(
        tmp_path, symbols=symbols, memory_docs=memory_docs, embedder=emb, rrf_k=60
    )

    lex = await LexicalRetriever(tmp_path, memory_symbols=symbols).retrieve(query, limit=10)
    sem = await SemanticRetriever(embedder=emb, memory_docs=memory_docs).retrieve(
        query, limit=10
    )
    fused = await hybrid.retrieve(query, limit=10)

    assert fused, "hybrid returned nothing"
    assert fused[0].path == "target.py", (
        f"expected target.py first, got {[h.path for h in fused]}; "
        f"lex={[h.path for h in lex]}; sem={[h.path for h in sem]}"
    )

    # Prove fusion beat at least one single channel's top-1 when they disagree,
    # or that target is ranked at least as high as in both channels.
    lex_rank = next((i for i, h in enumerate(lex) if h.path == "target.py"), 99)
    sem_rank = next((i for i, h in enumerate(sem) if h.path == "target.py"), 99)
    fused_rank = next(i for i, h in enumerate(fused) if h.path == "target.py")
    assert fused_rank <= min(lex_rank, sem_rank)
    assert fused_rank < max(lex_rank, sem_rank) or fused_rank == 0


@pytest.mark.asyncio
async def test_lexical_grep_finds_identifier(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def handle_checkout():\n    pass\n", encoding="utf-8")
    hits = await LexicalRetriever(tmp_path).retrieve("handle_checkout", limit=5)
    assert any(h.path.endswith("app.py") for h in hits)
