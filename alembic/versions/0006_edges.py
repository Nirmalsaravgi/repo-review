"""Phase 3 C1 — edges table (call graph + dynamic edges)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_edges"
down_revision: str | None = "0005_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "edges"


def upgrade() -> None:
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
            "src_symbol_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbols.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "dst_symbol_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbols.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # Denormalized target name — dynamic edges may resolve to no symbol row.
        sa.Column("dst_name", sa.String(512), nullable=True),
        # calls|imports|implements|extends|emits|subscribes|route
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        # import_resolved|name_match|string_literal|decorator|route_convention
        sa.Column("resolution_method", sa.String(32), nullable=False),
        # Denormalized for cheap per-file edge deletion on incremental patch.
        sa.Column(
            "src_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    # Reverse traversal (blast radius) and forward traversal (call flow).
    op.create_index("ix_edges_repo_dst", _TABLE, ["repo_id", "dst_symbol_id"])
    op.create_index("ix_edges_repo_src", _TABLE, ["repo_id", "src_symbol_id"])
    # Incremental patching by source file.
    op.create_index("ix_edges_src_file", _TABLE, ["src_file_id"])
    # Dynamic-edge join by target name (emit/subscribe string equality).
    op.create_index("ix_edges_repo_dstname", _TABLE, ["repo_id", "kind", "dst_name"])

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
    op.drop_index("ix_edges_repo_dstname", table_name=_TABLE)
    op.drop_index("ix_edges_src_file", table_name=_TABLE)
    op.drop_index("ix_edges_repo_src", table_name=_TABLE)
    op.drop_index("ix_edges_repo_dst", table_name=_TABLE)
    op.drop_table(_TABLE)
