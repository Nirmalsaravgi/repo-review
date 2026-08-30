from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover — tests may import models before optional install
    Vector = None  # type: ignore[misc, assignment]

from repo_core.db import Base

EMBEDDING_DIMS = 1024


class IndexStatus(StrEnum):
    PENDING = "pending"
    CLONING = "cloning"
    READY = "ready"
    ERROR = "error"
    REVOKED = "revoked"


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    github_org_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    users: Mapped[list["User"]] = relationship(back_populates="org")
    installations: Mapped[list["Installation"]] = relationship(back_populates="org")
    repositories: Mapped[list["Repository"]] = relationship(back_populates="org")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("org_id", "github_user_id", name="uq_users_org_github"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    github_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    login: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    org: Mapped[Org] = relationship(back_populates="users")


class Installation(Base):
    __tablename__ = "installations"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    github_installation_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(50), nullable=False, default="Organization")
    encrypted_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    org: Mapped[Org] = relationship(back_populates="installations")
    repositories: Mapped[list["Repository"]] = relationship(back_populates="installation")


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("org_id", "github_repo_id", name="uq_repos_org_github"),
        Index("ix_repositories_org_id", "org_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    installation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("installations.id", ondelete="SET NULL"), nullable=True
    )
    github_repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    clone_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_indexed_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=IndexStatus.PENDING.value
    )
    index_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_shallow: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    org: Mapped[Org] = relationship(back_populates="repositories")
    installation: Mapped[Installation | None] = relationship(back_populates="repositories")
    index_runs: Mapped[list["IndexRun"]] = relationship(back_populates="repository")


class IndexRun(Base):
    __tablename__ = "index_runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(64), nullable=False)  # manual|push|install
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    repository: Mapped[Repository] = relationship(back_populates="index_runs")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_repo_id", "repo_id"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="New conversation")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# --------------------------------------------------------------------------- #
# Phase 1 — git intelligence
# --------------------------------------------------------------------------- #
class FileRecord(Base):
    """Minimal file registry (Phase 1). Phase 2 enriches language/loc/symbols."""

    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("repo_id", "path", name="uq_files_repo_path"),
        Index("ix_files_repo_id", "repo_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    blob_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    loc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Author(Base):
    __tablename__ = "authors"
    __table_args__ = (
        UniqueConstraint("repo_id", "email", name="uq_authors_repo_email"),
        Index("ix_authors_repo_login", "repo_id", "github_login"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(320), nullable=True)
    github_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Commit(Base):
    __tablename__ = "commits"
    __table_args__ = (
        UniqueConstraint("repo_id", "sha", name="uq_commits_repo_sha"),
        Index("ix_commits_repo_committed", "repo_id", "committed_at"),
        Index("ix_commits_author", "author_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    sha: Mapped[str] = mapped_column(String(64), nullable=False)
    author_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("authors.id", ondelete="SET NULL"), nullable=True
    )
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CommitFile(Base):
    __tablename__ = "commit_files"
    __table_args__ = (Index("ix_commit_files_commit", "commit_id"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    commit_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("commits.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    additions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deletions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    change_type: Mapped[str] = mapped_column(String(16), nullable=False)  # added|modified|deleted|renamed


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint("repo_id", "number", name="uq_prs_repo_number"),
        Index("ix_pull_requests_repo_id", "repo_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    merge_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    author_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("authors.id", ondelete="SET NULL"), nullable=True
    )
    issue_refs: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)


class Ownership(Base):
    __tablename__ = "ownership"
    __table_args__ = (
        UniqueConstraint("repo_id", "path_prefix", "author_id", name="uq_ownership_key"),
        Index("ix_ownership_repo_prefix", "repo_id", "path_prefix"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    path_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("authors.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_touched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- #
# Phase 2 — persistent index (symbols first; chunks in 0005)
# --------------------------------------------------------------------------- #
class Symbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        Index("ix_symbols_repo_id", "repo_id"),
        Index("ix_symbols_file_id", "file_id"),
        Index("ix_symbols_repo_name", "repo_id", "name"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    parent_symbol_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("symbols.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    qualified_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    docstring: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    start_byte: Mapped[int] = mapped_column(Integer, nullable=False)
    end_byte: Mapped[int] = mapped_column(Integer, nullable=False)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_repo_id", "repo_id"),
        Index("ix_chunks_file_id", "file_id"),
        Index("ix_chunks_content_sha", "file_id", "content_sha"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    symbol_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("symbols.id", ondelete="SET NULL"), nullable=True
    )
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    header: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMS) if Vector is not None else Text, nullable=True
    )


# --------------------------------------------------------------------------- #
# Phase 3 — structure (call graph + dynamic edges)
# --------------------------------------------------------------------------- #
class Edge(Base):
    """A directed relationship between symbols (or files, for imports).

    `dst_symbol_id` is nullable: dynamic edges (emit/subscribe, routes) and
    unresolved call sites keep the target `dst_name` without a resolved symbol.
    Every edge carries `confidence` + `resolution_method` so the UI can be
    honest about approximate (name-match / string-literal) vs resolved edges.
    """

    __tablename__ = "edges"
    __table_args__ = (
        Index("ix_edges_repo_dst", "repo_id", "dst_symbol_id"),
        Index("ix_edges_repo_src", "repo_id", "src_symbol_id"),
        Index("ix_edges_src_file", "src_file_id"),
        Index("ix_edges_repo_dstname", "repo_id", "kind", "dst_name"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    src_symbol_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("symbols.id", ondelete="CASCADE"), nullable=True
    )
    dst_symbol_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("symbols.id", ondelete="CASCADE"), nullable=True
    )
    dst_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    resolution_method: Mapped[str] = mapped_column(String(32), nullable=False)
    src_file_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=True
    )


# --------------------------------------------------------------------------- #
# Phase 4 — PR review bot
# --------------------------------------------------------------------------- #
class PRReview(Base):
    """One review the bot computed for a PR at a given head SHA.

    Idempotency is `(repo_id, pr_number, head_sha)`: a new push is a new head
    SHA and gets a fresh single review. `findings` is the full computed set;
    `posted_count` is how many survived the threshold + cap. `dismissed` is the
    dismissal-rate numerator. Nothing is posted unless `pr_bot_enabled` is on —
    a `dry_run` row records what *would* have been posted.
    """

    __tablename__ = "pr_reviews"
    __table_args__ = (
        UniqueConstraint("repo_id", "pr_number", "head_sha", name="uq_pr_reviews_idem"),
        Index("ix_pr_reviews_repo_pr", "repo_id", "pr_number"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    findings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    posted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    github_review_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- #
# Understanding layer — brief, architecture, APIs, flows
# --------------------------------------------------------------------------- #
class Endpoint(Base):
    __tablename__ = "endpoints"
    __table_args__ = (Index("ix_endpoints_repo", "repo_id"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    handler_symbol_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("symbols.id", ondelete="SET NULL"), nullable=True
    )
    file_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=True
    )
    handler_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_hint: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    source: Mapped[str] = mapped_column(String(32), nullable=False)


class External(Base):
    __tablename__ = "externals"
    __table_args__ = (
        UniqueConstraint("repo_id", "name", name="uq_externals_repo_name"),
        Index("ix_externals_repo", "repo_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence: Mapped[list[str] | dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)


class Component(Base):
    __tablename__ = "components"
    __table_args__ = (
        UniqueConstraint("repo_id", "layer", "name", name="uq_components_repo_layer_name"),
        Index("ix_components_repo", "repo_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    layer: Mapped[str] = mapped_column(String(32), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(128), nullable=True)
    folder_globs: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    symbol_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Flow(Base):
    __tablename__ = "flows"
    __table_args__ = (Index("ix_flows_repo", "repo_id"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    seed_symbol_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("symbols.id", ondelete="SET NULL"), nullable=True
    )
    seed_endpoint_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("endpoints.id", ondelete="SET NULL"), nullable=True
    )
    steps: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    mermaid: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    indexed_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Brief(Base):
    __tablename__ = "briefs"
    __table_args__ = (
        UniqueConstraint("repo_id", "indexed_sha", name="uq_briefs_repo_sha"),
        Index("ix_briefs_repo", "repo_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    indexed_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    narrative: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


TENANT_TABLES = (
    "users",
    "installations",
    "repositories",
    "index_runs",
    "conversations",
    "messages",
    "files",
    "authors",
    "commits",
    "commit_files",
    "pull_requests",
    "ownership",
    "symbols",
    "chunks",
    "edges",
    "pr_reviews",
    "endpoints",
    "externals",
    "components",
    "flows",
    "briefs",
)
