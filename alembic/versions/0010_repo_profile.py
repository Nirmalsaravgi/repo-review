"""Repository profile — application style and selected analyzers."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010_repo_profile"
down_revision: str | None = "0009_local_repos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("repo_profiles",)


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
        "repo_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("indexed_sha", sa.String(64), nullable=True),
        sa.Column("application_style", sa.String(32), nullable=False),
        sa.Column("languages", postgresql.JSONB(), nullable=True),
        sa.Column("frameworks", postgresql.JSONB(), nullable=True),
        sa.Column("analyzers", postgresql.JSONB(), nullable=True),
        sa.Column("signals", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("repo_id", "indexed_sha", name="uq_repo_profiles_repo_sha"),
        sa.Index("ix_repo_profiles_repo", "repo_id"),
    )
    _rls_and_grants(_TABLES)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)


def _rls_and_grants(tables: tuple[str, ...]) -> None:
    for table in tables:
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
            GRANT SELECT, INSERT, UPDATE, DELETE ON {", ".join(tables)} TO repo_app;
          END IF;
        END $$;
        """
    )
