"""Phase 4 B1 — pr_reviews table (PR review bot findings + outcome)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_pr_reviews"
down_revision: str | None = "0006_edges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "pr_reviews"


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
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(64), nullable=False),
        # pending | posted | skipped | dry_run | error
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        # Full findings: [{check, severity, confidence, path, line, message, evidence}]
        sa.Column("findings", postgresql.JSONB(), nullable=True),
        # How many findings survived the threshold + cap and were shown.
        sa.Column("posted_count", sa.Integer(), nullable=False, server_default="0"),
        # The GitHub review id, when actually posted.
        sa.Column("github_review_id", sa.BigInteger(), nullable=True),
        # Dismissal-rate numerator: user resolved / deleted the review.
        sa.Column("dismissed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Idempotency: one review per (repo, PR, head sha) — synchronize won't double-post.
    op.create_index(
        "uq_pr_reviews_idem", _TABLE, ["repo_id", "pr_number", "head_sha"], unique=True
    )
    op.create_index("ix_pr_reviews_repo_pr", _TABLE, ["repo_id", "pr_number"])

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
    op.drop_index("ix_pr_reviews_repo_pr", table_name=_TABLE)
    op.drop_index("uq_pr_reviews_idem", table_name=_TABLE)
    op.drop_table(_TABLE)
