from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from repo_core.config import get_settings
from repo_core.db import get_db
from repo_core.schemas import HealthOut

router = APIRouter()


@router.get("/health", response_model=HealthOut)
async def health(db: AsyncSession = Depends(get_db)) -> HealthOut:
    settings = get_settings()
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_status = "error"
    return HealthOut(
        status="ok" if db_status == "ok" else "degraded",
        github_configured=settings.github_configured,
        database=db_status,
    )
