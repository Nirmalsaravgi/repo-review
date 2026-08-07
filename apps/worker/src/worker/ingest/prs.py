"""G3 — GraphQL PR backfill + commit ↔ PR linkage."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from repo_core.github_app import GitHubAppError, get_installation_token, graphql
from repo_core.models import Author, Commit, PullRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from worker.ingest.git_extract import parse_pr_numbers

logger = logging.getLogger(__name__)

_PRS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: [MERGED], first: 50, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        body
        mergedAt
        mergeCommit { oid }
        author { login }
        closingIssuesReferences(first: 20) {
          nodes { number }
        }
      }
    }
  }
}
"""


def split_full_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid repo full_name: {full_name}")
    return parts[0], parts[1]


def parse_pr_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize GraphQL PR nodes into plain dicts (testable without network)."""
    out: list[dict[str, Any]] = []
    for node in nodes:
        if not node or node.get("number") is None:
            continue
        merge = node.get("mergeCommit") or {}
        author = node.get("author") or {}
        issues = (node.get("closingIssuesReferences") or {}).get("nodes") or []
        out.append(
            {
                "number": int(node["number"]),
                "title": node.get("title"),
                "body": node.get("body"),
                "merged_at": node.get("mergedAt"),
                "merge_commit_sha": merge.get("oid"),
                "author_login": author.get("login"),
                "issue_refs": [int(i["number"]) for i in issues if i and i.get("number") is not None],
            }
        )
    return out


async def fetch_merged_prs(
    *,
    full_name: str,
    installation_id: int,
    max_pages: int = 40,
) -> list[dict[str, Any]]:
    owner, name = split_full_name(full_name)
    token = await get_installation_token(installation_id)
    cursor: str | None = None
    all_prs: list[dict[str, Any]] = []
    for _ in range(max_pages):
        try:
            data = await graphql(
                _PRS_QUERY,
                token=token,
                variables={"owner": owner, "name": name, "cursor": cursor},
            )
        except GitHubAppError:
            logger.exception("PR GraphQL failed for %s", full_name)
            break
        repo = (data or {}).get("repository") or {}
        connection = repo.get("pullRequests") or {}
        nodes = connection.get("nodes") or []
        all_prs.extend(parse_pr_nodes(nodes))
        page = connection.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
        if not cursor:
            break
    return all_prs


async def backfill_pull_requests(
    db: AsyncSession,
    *,
    org_id: UUID,
    repo_id: UUID,
    full_name: str,
    installation_id: int,
) -> dict[str, Any]:
    prs = await fetch_merged_prs(full_name=full_name, installation_id=installation_id)
    if not prs:
        linked = await _link_commits_from_messages(db, repo_id)
        return {"prs_upserted": 0, "commits_linked": linked}

    authors = await _authors_by_login(db, repo_id)
    existing = await db.execute(select(PullRequest).where(PullRequest.repo_id == repo_id))
    by_number = {p.number: p for p in existing.scalars().all()}

    upserted = 0
    merge_sha_to_number: dict[str, int] = {}
    for pr in prs:
        author_id = None
        login = pr.get("author_login")
        if login and login.lower() in authors:
            author_id = authors[login.lower()].id

        merged_at = _parse_dt(pr.get("merged_at"))
        row = by_number.get(pr["number"])
        if row is None:
            row = PullRequest(
                id=uuid4(),
                org_id=org_id,
                repo_id=repo_id,
                number=pr["number"],
                title=pr.get("title"),
                body=pr.get("body"),
                merged_at=merged_at,
                merge_commit_sha=pr.get("merge_commit_sha"),
                author_id=author_id,
                issue_refs=pr.get("issue_refs") or [],
            )
            db.add(row)
            by_number[pr["number"]] = row
        else:
            row.title = pr.get("title")
            row.body = pr.get("body")
            row.merged_at = merged_at
            row.merge_commit_sha = pr.get("merge_commit_sha")
            if author_id:
                row.author_id = author_id
            row.issue_refs = pr.get("issue_refs") or []
        upserted += 1
        if pr.get("merge_commit_sha"):
            merge_sha_to_number[pr["merge_commit_sha"]] = pr["number"]

    await db.flush()
    linked_merge = await _link_by_merge_sha(db, repo_id, merge_sha_to_number)
    linked_msg = await _link_commits_from_messages(db, repo_id)
    return {
        "prs_upserted": upserted,
        "commits_linked": linked_merge + linked_msg,
    }


async def _authors_by_login(db: AsyncSession, repo_id: UUID) -> dict[str, Author]:
    result = await db.execute(
        select(Author).where(Author.repo_id == repo_id, Author.github_login.is_not(None))
    )
    return {a.github_login.lower(): a for a in result.scalars().all() if a.github_login}


async def _link_by_merge_sha(
    db: AsyncSession, repo_id: UUID, merge_sha_to_number: dict[str, int]
) -> int:
    if not merge_sha_to_number:
        return 0
    linked = 0
    result = await db.execute(
        select(Commit).where(
            Commit.repo_id == repo_id,
            Commit.sha.in_(list(merge_sha_to_number.keys())),
        )
    )
    for commit in result.scalars().all():
        number = merge_sha_to_number.get(commit.sha)
        if number is not None and commit.pr_number != number:
            commit.pr_number = number
            linked += 1
    await db.flush()
    return linked


async def _link_commits_from_messages(db: AsyncSession, repo_id: UUID) -> int:
    result = await db.execute(
        select(Commit).where(Commit.repo_id == repo_id, Commit.pr_number.is_(None))
    )
    linked = 0
    for commit in result.scalars().all():
        nums = parse_pr_numbers(commit.message)
        if nums:
            commit.pr_number = nums[0]
            linked += 1
    await db.flush()
    return linked


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
