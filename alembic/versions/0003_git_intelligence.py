"""Phase 1 git-intelligence tables (files, authors, commits, commit_files,
pull_requests, ownership) with tenant RLS."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_git_intelligence"
down_revision: str | None = "0002_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("files", "authors", "commits", "commit_files", "pull_requests", "ownership")


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
        "files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("blob_sha", sa.String(64), nullable=True),
        sa.Column("language", sa.String(64), nullable=True),
        sa.Column("loc", sa.Integer(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("repo_id", "path", name="uq_files_repo_path"),
    )
    op.create_index("ix_files_repo_id", "files", ["repo_id"])

    op.create_table(
        "authors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(320), nullable=True),
        sa.Column("github_login", sa.String(255), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("repo_id", "email", name="uq_authors_repo_email"),
    )
    op.create_index("ix_authors_repo_login", "authors", ["repo_id", "github_login"])

    op.create_table(
        "commits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("sha", sa.String(64), nullable=False),
        sa.Column(
            "author_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("authors.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.UniqueConstraint("repo_id", "sha", name="uq_commits_repo_sha"),
    )
    op.create_index("ix_commits_repo_committed", "commits", ["repo_id", "committed_at"])
    op.create_index("ix_commits_author", "commits", ["author_id"])

    op.create_table(
        "commit_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        sa.Column(
            "commit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("additions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deletions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("change_type", sa.String(16), nullable=False),
    )
    op.create_index("ix_commit_files_commit", "commit_files", ["commit_id"])

    op.create_table(
        "pull_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merge_commit_sha", sa.String(64), nullable=True),
        sa.Column(
            "author_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("authors.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("issue_refs", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("repo_id", "number", name="uq_prs_repo_number"),
    )
    op.create_index("ix_pull_requests_repo_id", "pull_requests", ["repo_id"])

    op.create_table(
        "ownership",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _org(),
        _repo(),
        sa.Column("path_prefix", sa.Text(), nullable=False),
        sa.Column(
            "author_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("authors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_touched_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("repo_id", "path_prefix", "author_id", name="uq_ownership_key"),
    )
    op.create_index("ix_ownership_repo_prefix", "ownership", ["repo_id", "path_prefix"])

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
    op.drop_table("ownership")
    op.drop_table("pull_requests")
    op.drop_table("commit_files")
    op.drop_table("commits")
    op.drop_table("authors")
    op.drop_table("files")
