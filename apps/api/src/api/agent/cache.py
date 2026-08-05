"""Response cache for the agent loop, keyed on (repo_sha, normalized question).

The loop is the dominant cost driver; repeat questions on unchanged code must not
re-run it. `InMemoryResponseCache` keeps tests hermetic; `RedisResponseCache`
wraps an async redis client and is what the API wires up in Slice 4.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class ResponseCache(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...

    async def set(self, key: str, value: dict[str, Any], ttl: int) -> None: ...


class InMemoryResponseCache:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        return self._store.get(key)

    async def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        self._store[key] = value


class RedisResponseCache:
    """Adapter over an async redis client (redis.asyncio.Redis).

    Best-effort: a cache must never break the request path, so Redis failures are
    swallowed (a miss on read, a no-op on write) and logged.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = await self._client.get(key)
        except Exception as exc:  # noqa: BLE001 — cache is best-effort
            logger.warning("response cache get failed: %s", exc)
            return None
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        try:
            await self._client.set(key, json.dumps(value), ex=ttl)
        except Exception as exc:  # noqa: BLE001 — cache is best-effort
            logger.warning("response cache set failed: %s", exc)
