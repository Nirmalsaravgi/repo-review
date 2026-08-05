"""GitHub App user login + install bootstrap."""

from __future__ import annotations

import secrets
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import clear_session_cookie, get_optional_session, set_session_cookie
from repo_core.config import get_settings
from repo_core.db import current_org_id, get_db
from repo_core.github_app import (
    GitHubAppError,
    authorize_url,
    exchange_oauth_code,
    github_get,
    install_url,
    list_installation_repos,
)
from repo_core.models import Installation, Org, Repository, User
from repo_core.schemas import AuthUrlOut, MessageOut, OrgOut, SessionOut, UserOut
from repo_core.session import SessionData

router = APIRouter()


@router.get("/login", response_model=AuthUrlOut)
async def login() -> AuthUrlOut:
    settings = get_settings()
    if not settings.github_configured:
        raise HTTPException(
            status_code=503,
            detail="GitHub App is not configured. Set GITHUB_APP_* env vars and private key.",
        )
    state = secrets.token_urlsafe(24)
    return AuthUrlOut(url=authorize_url(state))


@router.get("/callback")
async def callback(
    code: str = Query(...),
    state: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Handle GitHub App user OAuth callback, then redirect to the web app."""
    settings = get_settings()
    try:
        token_payload = await exchange_oauth_code(code)
        access_token = token_payload["access_token"]
        gh_user = await github_get("/user", token=access_token)
        emails = await github_get("/user/emails", token=access_token)
        primary_email = next(
            (e["email"] for e in emails if e.get("primary") and e.get("verified")),
            gh_user.get("email"),
        )

        installations = await github_get("/user/installations", token=access_token)
        install_list = installations.get("installations") or []

        if install_list:
            inst = install_list[0]
            account = inst["account"]
            org = await _upsert_org(db, github_org_id=account["id"], name=account["login"])
            await db.execute(
                text("SELECT set_config('app.current_org_id', :org_id, true)"),
                {"org_id": str(org.id)},
            )
            current_org_id.set(org.id)
            installation = await _upsert_installation(
                db,
                org=org,
                github_installation_id=inst["id"],
                account_login=account["login"],
                account_type=account.get("type") or "User",
            )
            await _sync_repos(db, org=org, installation=installation)
        else:
            org = await _upsert_org(db, github_org_id=gh_user["id"], name=gh_user["login"])
            await db.execute(
                text("SELECT set_config('app.current_org_id', :org_id, true)"),
                {"org_id": str(org.id)},
            )
            current_org_id.set(org.id)
            installation = None

        user = await _upsert_user(
            db,
            org=org,
            github_user_id=gh_user["id"],
            login=gh_user["login"],
            email=primary_email,
            avatar_url=gh_user.get("avatar_url"),
        )
        await db.flush()

        session = SessionData(
            user_id=str(user.id),
            org_id=str(org.id),
            github_user_id=user.github_user_id,
            login=user.login,
        )
        redirect = RedirectResponse(url=f"{settings.web_base_url.rstrip('/')}/", status_code=302)
        set_session_cookie(redirect, session)
        if installation is None:
            redirect.set_cookie("needs_install", "1", max_age=600, httponly=False, samesite="lax")
        return redirect
    except GitHubAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/logout", response_model=MessageOut)
async def logout(response: Response) -> MessageOut:
    clear_session_cookie(response)
    return MessageOut(message="logged out")


@router.get("/me", response_model=SessionOut)
async def me(
    session: Annotated[SessionData | None, Depends(get_optional_session)],
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    settings = get_settings()
    base = SessionOut(
        authenticated=False,
        github_configured=settings.github_configured,
        install_url=install_url(settings) if settings.github_app_slug else None,
    )
    if session is None:
        return base

    current_org_id.set(session.org_uuid)
    await db.execute(
        text("SELECT set_config('app.current_org_id', :org_id, true)"),
        {"org_id": str(session.org_uuid)},
    )

    user = await db.get(User, session.user_uuid)
    org = await db.get(Org, session.org_uuid)
    if user is None or org is None:
        return base

    return SessionOut(
        authenticated=True,
        user=UserOut.model_validate(user),
        org=OrgOut.model_validate(org),
        github_configured=settings.github_configured,
        install_url=install_url(settings) if settings.github_app_slug else None,
    )


async def _upsert_org(db: AsyncSession, *, github_org_id: int, name: str) -> Org:
    result = await db.execute(select(Org).where(Org.github_org_id == github_org_id))
    org = result.scalar_one_or_none()
    if org:
        org.name = name
        return org
    org = Org(id=uuid4(), github_org_id=github_org_id, name=name)
    db.add(org)
    await db.flush()
    return org


async def _upsert_user(
    db: AsyncSession,
    *,
    org: Org,
    github_user_id: int,
    login: str,
    email: str | None,
    avatar_url: str | None,
) -> User:
    result = await db.execute(
        select(User).where(User.org_id == org.id, User.github_user_id == github_user_id)
    )
    user = result.scalar_one_or_none()
    if user:
        user.login = login
        user.email = email
        user.avatar_url = avatar_url
        return user
    user = User(
        id=uuid4(),
        org_id=org.id,
        github_user_id=github_user_id,
        login=login,
        email=email,
        avatar_url=avatar_url,
    )
    db.add(user)
    await db.flush()
    return user


async def _upsert_installation(
    db: AsyncSession,
    *,
    org: Org,
    github_installation_id: int,
    account_login: str,
    account_type: str,
) -> Installation:
    result = await db.execute(
        select(Installation).where(Installation.github_installation_id == github_installation_id)
    )
    inst = result.scalar_one_or_none()
    if inst:
        inst.account_login = account_login
        inst.account_type = account_type
        inst.org_id = org.id
        return inst
    inst = Installation(
        id=uuid4(),
        org_id=org.id,
        github_installation_id=github_installation_id,
        account_login=account_login,
        account_type=account_type,
    )
    db.add(inst)
    await db.flush()
    return inst


async def _sync_repos(db: AsyncSession, *, org: Org, installation: Installation) -> None:
    repos = await list_installation_repos(installation.github_installation_id)
    for gh in repos:
        result = await db.execute(
            select(Repository).where(
                Repository.org_id == org.id,
                Repository.github_repo_id == gh["id"],
            )
        )
        row = result.scalar_one_or_none()
        if row:
            row.full_name = gh["full_name"]
            row.default_branch = gh.get("default_branch") or "main"
            row.private = bool(gh.get("private"))
            row.installation_id = installation.id
            continue
        db.add(
            Repository(
                id=uuid4(),
                org_id=org.id,
                installation_id=installation.id,
                github_repo_id=gh["id"],
                full_name=gh["full_name"],
                default_branch=gh.get("default_branch") or "main",
                private=bool(gh.get("private")),
            )
        )
    await db.flush()
