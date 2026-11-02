"""Local repository source — nullable github_repo_id + source column."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_local_repos"
down_revision: str | None = "0008_understanding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("source", sa.String(16), nullable=False, server_default="github"),
    )
    op.alter_column(
        "repositories",
        "github_repo_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.drop_constraint("uq_repos_org_github", "repositories", type_="unique")
    op.create_index(
        "uq_repos_org_github",
        "repositories",
        ["org_id", "github_repo_id"],
        unique=True,
        postgresql_where=sa.text("github_repo_id IS NOT NULL"),
    )
    op.create_index(
        "uq_repos_org_local_path",
        "repositories",
        ["org_id", "clone_path"],
        unique=True,
        postgresql_where=sa.text("source = 'local'"),
    )


def downgrade() -> None:
    op.drop_index("uq_repos_org_local_path", table_name="repositories")
    op.drop_index("uq_repos_org_github", table_name="repositories")
    op.create_unique_constraint("uq_repos_org_github", "repositories", ["org_id", "github_repo_id"])
    op.alter_column(
        "repositories",
        "github_repo_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_column("repositories", "source")
