"""Understanding layer — endpoints, externals, components, flows, briefs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_understanding"
down_revision: str | None = "0007_pr_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("endpoints", "externals", "components", "flows", "briefs")


def _org() -> sa.Column:
    return sa.Column(
        "org_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("orgs.id", ondelete="CASCADE"),
        nullable=False,
    )


def _repo() -> sa.Column:
    return sa.Column(
        "repo_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "endpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column(
            "handler_symbol_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbols.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("handler_name", sa.String(512), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("auth_hint", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Index("ix_endpoints_repo", "repo_id"),
    )

    op.create_table(
        "externals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Index("ix_externals_repo", "repo_id"),
        sa.UniqueConstraint("repo_id", "name", name="uq_externals_repo_name"),
    )

    op.create_table(
        "components",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("layer", sa.String(32), nullable=False),
        sa.Column("domain", sa.String(128), nullable=True),
        sa.Column("folder_globs", postgresql.JSONB(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("indexed_sha", sa.String(64), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("symbol_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Index("ix_components_repo", "repo_id"),
        sa.UniqueConstraint("repo_id", "layer", "name", name="uq_components_repo_layer_name"),
    )

    op.create_table(
        "flows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "seed_symbol_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbols.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "seed_endpoint_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("endpoints.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("steps", postgresql.JSONB(), nullable=True),
        sa.Column("mermaid", sa.Text(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("file_ids", postgresql.JSONB(), nullable=True),
        sa.Column("indexed_sha", sa.String(64), nullable=True),
        sa.Index("ix_flows_repo", "repo_id"),
    )

    op.create_table(
        "briefs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("indexed_sha", sa.String(64), nullable=True),
        sa.Column("facts", postgresql.JSONB(), nullable=False),
        sa.Column("narrative", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Index("ix_briefs_repo", "repo_id"),
        sa.UniqueConstraint("repo_id", "indexed_sha", name="uq_briefs_repo_sha"),
    )

    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (org_id::text = current_setting('app.current_org_id', true))
            WITH CHECK (org_id::text = current_setting('app.current_org_id', true))
            """
        )

    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'repo_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON {", ".join(_TABLES)} TO repo_app;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)
