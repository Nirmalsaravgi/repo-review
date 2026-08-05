"""Shared async Redis client (queue broker in later phases; response cache now)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import redis.asyncio as aioredis
from repo_core.config import get_settings


@lru_cache
def get_redis() -> Any:
    settings = get_settings()
    return aioredis.from_url(settings.redis_url, decode_responses=True)
