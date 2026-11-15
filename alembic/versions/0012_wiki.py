"""Documentation plans, atomic summaries, wiki pages, and validation results."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_wiki"
down_revision: str | None = "0011_modules_features"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("doc_plans", "atomic_summaries", "wiki_artifacts", "validation_results")


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
        "doc_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("indexed_sha", sa.String(64), nullable=True),
        sa.Column("categories", postgresql.JSONB(), nullable=True),
        sa.Column("priorities", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("repo_id", "indexed_sha", name="uq_doc_plans_repo_sha"),
        sa.Index("ix_doc_plans_repo", "repo_id"),
    )
    op.create_table(
        "atomic_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("indexed_sha", sa.String(64), nullable=True),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("facts", postgresql.JSONB(), nullable=True),
        sa.Column("inferences", postgresql.JSONB(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("content_sha", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "repo_id",
            "target_kind",
            "target_id",
            "content_sha",
            name="uq_atomic_summaries_target_sha",
        ),
        sa.Index("ix_atomic_summaries_target", "repo_id", "target_kind", "target_id"),
    )
    op.create_table(
        "wiki_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("indexed_sha", sa.String(64), nullable=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("slug", sa.String(256), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("parent_slug", sa.String(256), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("diagram_ir", postgresql.JSONB(), nullable=True),
        sa.Column("source_revision", sa.String(64), nullable=True),
        sa.Column("depends_on", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="staging"),
        sa.Column("validation_status", sa.String(16), nullable=False, server_default="unchecked"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("repo_id", "indexed_sha", "slug", name="uq_wiki_artifacts_repo_sha_slug"),
        sa.Index("ix_wiki_artifacts_repo_status", "repo_id", "status", "type"),
    )
    op.create_table(
        "validation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("indexed_sha", sa.String(64), nullable=True),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wiki_artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("check_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Index("ix_validation_results_artifact", "artifact_id"),
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
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'repo_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON doc_plans, atomic_summaries, wiki_artifacts, validation_results TO repo_app;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for table in ("validation_results", "wiki_artifacts", "atomic_summaries", "doc_plans"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)
