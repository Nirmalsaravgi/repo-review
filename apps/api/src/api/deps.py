"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from repo_core.config import get_settings
from repo_core.db import current_org_id, get_db
from repo_core.session import SessionData, dump_session, load_session


async def get_optional_session(request: Request) -> SessionData | None:
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    return load_session(raw)


async def require_session(
    session: Annotated[SessionData | None, Depends(get_optional_session)],
) -> SessionData:
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


async def tenant_db(
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AsyncSession:
    """DB session with RLS tenant context applied."""
    current_org_id.set(session.org_uuid)
    await db.execute(
        text("SELECT set_config('app.current_org_id', :org_id, true)"),
        {"org_id": str(session.org_uuid)},
    )
    return db


def set_session_cookie(response: Response, session: SessionData) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=dump_session(session),
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.app_env != "development",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/")
