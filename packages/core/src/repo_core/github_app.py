"""GitHub App authentication helpers (JWT + installation tokens)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import jwt

from repo_core.config import Settings, get_settings

GITHUB_API = "https://api.github.com"


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


async def list_installation_repos(installation_id: int) -> list[dict[str, Any]]:
    token = await get_installation_token(installation_id)
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        data = await github_get(
            f"/installation/repositories",
            token=token,
            params={"per_page": 100, "page": page},
        )
        batch = data.get("repositories") or []
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


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
