"""Celery + asyncio helpers.

Celery tasks call ``asyncio.run()`` per job. The shared SQLAlchemy async engine
keeps pooled asyncpg connections bound to the loop that created them. When that
loop closes, the next task pings dead connections and fails with
``Event loop is closed`` / ``NoneType has no attribute 'send'``.

Dispose the engine after each task so the next ``asyncio.run`` opens fresh
connections on the new loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

from repo_core.db import engine

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    async def _wrapped() -> T:
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_wrapped())
