"""Phase 2 P3 — chunks table with pgvector HNSW embeddings."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_chunks"
down_revision: str | None = "0004_symbols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunks"
_EMBED_DIMS = 1024


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "repo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "symbol_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbols.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("header", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(_EMBED_DIMS), nullable=True),
    )
    op.create_index("ix_chunks_repo_id", _TABLE, ["repo_id"])
    op.create_index("ix_chunks_file_id", _TABLE, ["file_id"])
    op.create_index("ix_chunks_content_sha", _TABLE, ["file_id", "content_sha"])
    op.execute(
        f"""
        CREATE INDEX ix_chunks_embedding_hnsw ON {_TABLE}
        USING hnsw (embedding vector_cosine_ops)
        """
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
        USING (org_id::text = current_setting('app.current_org_id', true))
        WITH CHECK (org_id::text = current_setting('app.current_org_id', true))
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'repo_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO repo_app;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.drop_index("ix_chunks_content_sha", table_name=_TABLE)
    op.drop_index("ix_chunks_file_id", table_name=_TABLE)
    op.drop_index("ix_chunks_repo_id", table_name=_TABLE)
    op.drop_table(_TABLE)
