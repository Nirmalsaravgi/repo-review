"""Phase 2 P3 — chunking, mock embeddings, in-memory semantic rank."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest
from repo_core.models import EMBEDDING_DIMS, TENANT_TABLES, Chunk
from repo_parsing.chunking import build_scope_header, chunk_file_symbols, scrub_pg_text
from repo_providers import MockEmbeddingProvider, build_embedding_provider
from worker.ingest.semantic import cosine_distance, rank_by_embedding


@dataclass
class _Sym:
    id: object
    name: str
    kind: str
    signature: str | None
    qualified_name: str | None
    parent_symbol_id: object | None
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int


def test_chunks_in_tenant_tables() -> None:
    assert "chunks" in TENANT_TABLES


def test_chunk_model_construct() -> None:
    org, repo, file_id = uuid4(), uuid4(), uuid4()
    ch = Chunk(
        id=uuid4(),
        org_id=org,
        repo_id=repo,
        file_id=file_id,
        start_line=1,
        end_line=5,
        header="// File: x.py",
        content="def f():\n    pass\n",
        content_sha="abc",
        embedding=None,
    )
    assert ch.start_line == 1
    assert EMBEDDING_DIMS == 1024


def test_scope_header_includes_called_by_placeholder() -> None:
    h = build_scope_header(
        repo_full_name="acme/app",
        path="src/a.py",
        language="python",
        start_line=1,
        end_line=10,
        kind="function",
        name="checkout",
        signature="def checkout():",
        imports=["import os"],
    )
    assert "Repo: acme/app" in h
    assert "Called by:" in h
    assert "Imports: import os" in h


def test_chunk_file_top_level_symbols() -> None:
    source = (
        b"import os\n\n"
        b"def alpha():\n"
        b"    '''Alpha does payment checkout with stripe card charge.'''\n"
        b"    return charge()\n\n"
        b"def beta():\n"
        b"    '''Beta handles login session cookies and password auth.'''\n"
        b"    return login()\n"
    )
    a_start = source.index(b"def alpha")
    a_end = source.index(b"\n\ndef beta")
    b_start = source.index(b"def beta")
    b_end = len(source)
    syms = [
        _Sym(uuid4(), "os", "import", "import os", "os", None, 1, 1, 0, 9),
        _Sym(uuid4(), "alpha", "function", "def alpha():", "alpha", None, 3, 5, a_start, a_end),
        _Sym(uuid4(), "beta", "function", "def beta():", "beta", None, 7, 9, b_start, b_end),
    ]
    chunks = chunk_file_symbols(
        repo_full_name="t/r",
        path="m.py",
        language="python",
        source=source,
        symbols=syms,
    )
    assert len(chunks) == 2
    assert any("alpha" in c.header for c in chunks)
    assert any("beta" in c.header for c in chunks)
    assert all(c.content_sha for c in chunks)
    assert all(c.embed_text.startswith("// Repo:") for c in chunks)


def test_scrub_pg_text_strips_nul() -> None:
    assert scrub_pg_text("ok") == "ok"
    assert scrub_pg_text("a\x00b\x00c") == "abc"


def test_chunk_file_strips_nul_bytes() -> None:
    source = b"def alpha():\n    return '\x00bad'\n"
    chunks = chunk_file_symbols(
        repo_full_name="t/r",
        path="m.py",
        language="python",
        source=source,
        symbols=[],
    )
    assert chunks
    assert "\x00" not in chunks[0].content
    assert "\x00" not in chunks[0].embed_text


@pytest.mark.asyncio
async def test_mock_embedding_deterministic() -> None:
    emb = MockEmbeddingProvider(dimensions=32)
    a = await emb.embed(["hello world"])
    b = await emb.embed(["hello world"])
    c = await emb.embed(["different"])
    assert a[0] == b[0]
    assert a[0] != c[0]
    assert abs(sum(x * x for x in a[0]) - 1.0) < 1e-5


@pytest.mark.asyncio
async def test_build_embedding_provider_defaults_to_mock() -> None:
    p = build_embedding_provider(provider="", api_key="")
    assert isinstance(p, MockEmbeddingProvider)
    vecs = await p.embed(["x"])
    assert len(vecs[0]) == 1024


@pytest.mark.asyncio
async def test_semantic_rank_prefers_similar_text() -> None:
    emb = MockEmbeddingProvider(dimensions=64)
    docs = {
        "pay": "function chargeCard processes stripe payment checkout",
        "auth": "function loginUser validates password session cookie",
        "noise": "render css grid layout flexbox",
    }
    vectors = {k: (await emb.embed([v]))[0] for k, v in docs.items()}
    q = (await emb.embed(["how does payment checkout work with stripe"]))[0]
    ranked = rank_by_embedding(q, list(vectors.items()), limit=3)
    assert ranked[0][0] == "pay"
    assert ranked[0][1] < ranked[-1][1]
    assert cosine_distance(q, vectors["pay"]) < cosine_distance(q, vectors["noise"])
