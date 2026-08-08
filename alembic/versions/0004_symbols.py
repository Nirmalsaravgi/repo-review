"""Phase 2 P1 — symbols table with tenant RLS and trigram name index."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_symbols"
down_revision: str | None = "0003_git_intelligence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "symbols"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

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
            "parent_symbol_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbols.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("qualified_name", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("docstring", sa.Text(), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("start_byte", sa.Integer(), nullable=False),
        sa.Column("end_byte", sa.Integer(), nullable=False),
    )
    op.create_index("ix_symbols_repo_id", _TABLE, ["repo_id"])
    op.create_index("ix_symbols_file_id", _TABLE, ["file_id"])
    op.create_index("ix_symbols_repo_name", _TABLE, ["repo_id", "name"])
    op.execute(
        "CREATE INDEX ix_symbols_name_trgm ON symbols USING gin (name gin_trgm_ops)"
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
    op.drop_index("ix_symbols_name_trgm", table_name=_TABLE)
    op.drop_index("ix_symbols_repo_name", table_name=_TABLE)
    op.drop_index("ix_symbols_file_id", table_name=_TABLE)
    op.drop_index("ix_symbols_repo_id", table_name=_TABLE)
    op.drop_table(_TABLE)
