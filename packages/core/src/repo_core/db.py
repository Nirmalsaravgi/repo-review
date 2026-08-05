from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from repo_core.config import get_settings

# Tenant context set per-request; used for RLS SET LOCAL.
current_org_id: ContextVar[UUID | None] = ContextVar("current_org_id", default=None)


class Base(DeclarativeBase):
    pass


def create_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        echo=settings.app_env == "development",
    )


engine = create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        org_id = current_org_id.get()
        if org_id is not None:
            await session.execute(
                text("SELECT set_config('app.current_org_id', :org_id, true)"),
                {"org_id": str(org_id)},
            )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope(org_id: UUID | None = None) -> AsyncGenerator[AsyncSession, None]:
    token = current_org_id.set(org_id) if org_id is not None else None
    try:
        async with SessionLocal() as session:
            if org_id is not None:
                await session.execute(
                    text("SELECT set_config('app.current_org_id', :org_id, true)"),
                    {"org_id": str(org_id)},
                )
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    finally:
        if token is not None:
            current_org_id.reset(token)
