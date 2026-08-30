"""GitHub App authentication helpers (JWT + installation tokens)."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import jwt

from repo_core.config import Settings, get_settings

GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"
logger = logging.getLogger(__name__)


class GitHubAppError(RuntimeError):
    pass


def _load_private_key(path: Path) -> str:
    if not path.exists():
        raise GitHubAppError(f"GitHub App private key not found at {path}")
    return path.read_text(encoding="utf-8")


def build_app_jwt(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if not settings.github_app_id:
        raise GitHubAppError("GITHUB_APP_ID is not configured")
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (9 * 60),
        "iss": settings.github_app_id,
    }
    key = _load_private_key(settings.github_app_private_key_path)
    return jwt.encode(payload, key, algorithm="RS256")


async def get_installation_token(installation_id: int, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    token = build_app_jwt(settings)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers)
        if resp.status_code >= 400:
            raise GitHubAppError(
                f"Failed to create installation token: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        return data["token"]


async def exchange_oauth_code(code: str, settings: Settings | None = None) -> dict[str, Any]:
    """Exchange user OAuth code (GitHub App user authorization) for an access token."""
    settings = settings or get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_app_client_id,
                "client_secret": settings.github_app_client_secret,
                "code": code,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise GitHubAppError(data.get("error_description") or data["error"])
        return data


async def github_get(
    path: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
) -> Any:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{GITHUB_API}{path}", headers=headers, params=params)
        if resp.status_code >= 400:
            raise GitHubAppError(f"GitHub GET {path} failed: {resp.status_code} {resp.text}")
        return resp.json()


async def github_post(
    path: str,
    *,
    token: str,
    json: dict[str, Any] | None = None,
) -> Any:
    """POST to the GitHub REST API (write path — used by the PR review bot)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{GITHUB_API}{path}", headers=headers, json=json or {})
        if resp.status_code >= 400:
            raise GitHubAppError(f"GitHub POST {path} failed: {resp.status_code} {resp.text}")
        return resp.json()


async def github_get_paginated(
    path: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
    max_pages: int = 20,
) -> list[Any]:
    """GET a paginated list endpoint (per_page=100), returning the flattened list."""
    out: list[Any] = []
    page = 1
    base_params = dict(params or {})
    base_params.setdefault("per_page", 100)
    while page <= max_pages:
        base_params["page"] = page
        batch = await github_get(path, token=token, params=base_params)
        if not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(batch) < base_params["per_page"]:
            break
        page += 1
    return out


async def list_installation_repos(installation_id: int) -> list[dict[str, Any]]:
    token = await get_installation_token(installation_id)
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        data = await github_get(
            "/installation/repositories",
            token=token,
            params={"per_page": 100, "page": page},
        )
        batch = data.get("repositories") or []
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


async def graphql(
    query: str,
    *,
    token: str,
    variables: dict[str, Any] | None = None,
    max_retries: int = 5,
) -> dict[str, Any]:
    """Run a GitHub GraphQL query with secondary-rate-limit backoff."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"query": query, "variables": variables or {}}
    delay = 1.0
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(max_retries + 1):
            resp = await client.post(GITHUB_GRAPHQL, headers=headers, json=payload)
            if resp.status_code == 403 and (
                "secondary rate limit" in resp.text.lower()
                or resp.headers.get("retry-after")
            ):
                retry_after = float(resp.headers.get("retry-after") or delay)
                logger.warning("GitHub secondary rate limit; sleeping %.1fs", retry_after)
                await asyncio.sleep(retry_after)
                delay = min(delay * 2, 60.0)
                continue
            if resp.status_code == 502 or resp.status_code == 503:
                if attempt >= max_retries:
                    raise GitHubAppError(f"GraphQL failed: {resp.status_code} {resp.text}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            if resp.status_code >= 400:
                raise GitHubAppError(f"GraphQL failed: {resp.status_code} {resp.text}")
            data = resp.json()
            if "errors" in data and not data.get("data"):
                raise GitHubAppError(f"GraphQL errors: {data['errors']}")
            return data.get("data") or {}
    raise GitHubAppError("GraphQL exhausted retries")


def install_url(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    slug = settings.github_app_slug or "your-app"
    return f"https://github.com/apps/{slug}/installations/new"


def authorize_url(state: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    redirect = f"{settings.api_base_url.rstrip('/')}/auth/callback"
    return (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_app_client_id}"
        f"&redirect_uri={redirect}"
        f"&state={state}"
        "&allow_signup=false"
    )
