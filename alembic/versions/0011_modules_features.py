"""Hierarchical modules and cross-cutting feature candidates."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_modules_features"
down_revision: str | None = "0010_repo_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("modules", "features")


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
        "modules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("indexed_sha", sa.String(64), nullable=True),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("member_file_ids", postgresql.JSONB(), nullable=True),
        sa.Column("member_symbol_ids", postgresql.JSONB(), nullable=True),
        sa.Column("cohesion", sa.Float(), nullable=False, server_default="0"),
        sa.Column("coupling", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("symbol_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["parent_id"], ["modules.id"], ondelete="CASCADE"),
        sa.Index("ix_modules_repo_parent", "repo_id", "parent_id"),
    )
    op.create_table(
        "features",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("indexed_sha", sa.String(64), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("entry_point_symbol_ids", postgresql.JSONB(), nullable=True),
        sa.Column("member_symbol_ids", postgresql.JSONB(), nullable=True),
        sa.Column("module_ids", postgresql.JSONB(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Index("ix_features_repo", "repo_id"),
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
            GRANT SELECT, INSERT, UPDATE, DELETE ON modules, features TO repo_app;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for table in ("features", "modules"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)
